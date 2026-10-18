// In-app calls between teammates (docs/in-app-calls.md): the provider that keeps this tab's call
// stream open, rings for incoming calls and runs the WebRTC session, and the call screen
// (incoming, ringing, connecting, connected with timer, mute, speaker, camera, hang up, ended).
// Calls off on the server, a refused microphone, offline and failed calls each say what happened.
import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { Camera, CameraOff, Mic, MicOff, Minimize2, Maximize2, Phone, PhoneOff, Video, Volume2, VolumeX, X } from 'lucide-react';
import type { AppCall, AppCallConfig, AppCallMedia, AppCallSignal } from '@anum/contracts';
import * as callsApi from './lib/appCallsApi';
import { AppCallEngine, openLocalMedia } from './lib/appCallEngine';
import { elapsedSeconds, formatDuration, initialsOf, mediaProblem, newClientId, outcomeWord, phaseFor, type CallPhase } from './lib/appCalls';
import { describeError, isPermissionDenied } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { useModal } from './Dialog';
import './appCalls.css';

export type CallConfigState =
  | { state: 'loading' } | { state: 'ready'; config: AppCallConfig } | { state: 'denied' } | { state: 'error'; message: string };

interface Person { user_id: string; display_name: string }

interface CallsContext {
  config: CallConfigState;
  /** Why a call cannot be placed right now, or null. */
  unavailable: string | null;
  inCall: boolean;
  start(person: Person, media: AppCallMedia): void;
  /** Bumped whenever a call starts, changes or ends, so lists can reload. */
  revision: number;
}

const Context = createContext<CallsContext | null>(null);
export function useAppCalls(): CallsContext | null { return useContext(Context); }

interface Active {
  call: AppCall;
  role: 'caller' | 'callee';
  engine: AppCallEngine | null;
  offered: boolean;
}

type Ended = { call: AppCall; message: string | null; person: Person } | null;

function endMessage(call: AppCall): string | null {
  const word = outcomeWord(call);
  const name = call.peer_name;
  if (word === 'declinedByPeer') return t('appCalls.ended.declinedByPeer', { name });
  if (word === 'busyPeer') return t('appCalls.ended.busyPeer', { name });
  if (word === 'noAnswer') return t('appCalls.ended.noAnswer', { name });
  if (word === 'failed') return t('appCalls.ended.failed');
  if (word === 'dropped') return t('appCalls.ended.dropped');
  if (word === 'completed' && call.duration_seconds !== null) return t('appCalls.ended.duration', { duration: formatDuration(call.duration_seconds) });
  return null;
}

function mediaMessage(error: unknown): string {
  const problem = mediaProblem(error);
  if (problem === 'other') return t('appCalls.media.other', { message: error instanceof Error ? error.message : String(error) });
  return t(`appCalls.media.${problem}` as MessageKey);
}

/** A soft two-tone ring (incoming) or ring-back (outgoing) from Web Audio; silent if blocked. */
function useTone(kind: 'incoming' | 'ringback' | null) {
  useEffect(() => {
    if (!kind) return;
    let context: AudioContext | null = null;
    try { context = new AudioContext(); } catch { return; }
    const ctx = context;
    const beep = () => {
      try {
        const now = ctx.currentTime;
        for (const [offset, frequency] of kind === 'incoming' ? [[0, 660], [0.35, 880]] : [[0, 440]]) {
          const osc = ctx.createOscillator(), gain = ctx.createGain();
          osc.frequency.value = frequency;
          gain.gain.setValueAtTime(0.0001, now + offset);
          gain.gain.exponentialRampToValueAtTime(0.08, now + offset + 0.03);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.3);
          osc.connect(gain).connect(ctx.destination);
          osc.start(now + offset); osc.stop(now + offset + 0.32);
        }
      } catch { /* a ring is optional */ }
    };
    beep();
    const timer = setInterval(beep, kind === 'incoming' ? 2000 : 3000);
    return () => { clearInterval(timer); void ctx.close().catch(() => undefined); };
  }, [kind]);
}

export function AppCallProvider({ children }: { children: ReactNode }) {
  const clientId = useRef(newClientId()).current;
  const [config, setConfig] = useState<CallConfigState>({ state: 'loading' });
  const [online, setOnline] = useState(() => typeof navigator === 'undefined' || navigator.onLine !== false);
  const [active, setActive] = useState<Active | null>(null);
  const [preparing, setPreparing] = useState<{ person: Person; media: AppCallMedia } | null>(null);
  const [incoming, setIncoming] = useState<AppCall | null>(null);
  const [ended, setEnded] = useState<Ended>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'reconnecting' | 'failed'>('connecting');
  const [remote, setRemote] = useState<MediaStream | null>(null);
  const [revision, setRevision] = useState(0);
  const activeRef = useRef<Active | null>(null);
  const incomingRef = useRef<AppCall | null>(null);
  const queued = useRef(new Map<string, AppCallSignal[]>());
  activeRef.current = active;
  incomingRef.current = incoming;
  const bump = useCallback(() => setRevision((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    callsApi.getCallConfig().then(
      (value) => { if (!cancelled) setConfig({ state: 'ready', config: value }); },
      (error: unknown) => { if (!cancelled) setConfig(isPermissionDenied(error) ? { state: 'denied' } : { state: 'error', message: describeError(error) }); },
    );
    const up = () => setOnline(true), down = () => setOnline(false);
    window.addEventListener('online', up); window.addEventListener('offline', down);
    return () => { cancelled = true; window.removeEventListener('online', up); window.removeEventListener('offline', down); };
  }, []);

  const teardown = useCallback(() => {
    activeRef.current?.engine?.close();
    activeRef.current = null;
    setActive(null);
    setRemote(null);
    setConnection('connecting');
  }, []);

  const finish = useCallback((call: AppCall) => {
    const person = { user_id: call.peer_user_id, display_name: call.peer_name };
    teardown();
    setEnded({ call, message: endMessage(call), person });
    bump();
  }, [teardown, bump]);

  const hangUp = useCallback(async (reason: 'hangup' | 'failed' = 'hangup') => {
    const current = activeRef.current;
    if (!current) return;
    try { finish(await callsApi.endCall(current.call.id, reason)); }
    catch { finish({ ...current.call, status: 'ended', outcome: reason === 'failed' ? 'failed' : current.call.outcome }); }
  }, [finish]);

  const engineFor = useCallback(async (call: AppCall, role: 'caller' | 'callee', local: MediaStream) => {
    const servers = await callsApi.getIceServers();
    const engine = new AppCallEngine(role, local, servers, {
      sendDescription: (kind, sdp) => callsApi.sendDescription(call.id, kind, sdp),
      sendCandidates: (candidates) => callsApi.sendCandidates(call.id, candidates),
      heartbeat: () => callsApi.sendHeartbeat(call.id),
    }, {
      onRemoteStream: setRemote,
      onConnection: (state) => {
        setConnection(state);
        if (state === 'failed') void hangUp('failed');
      },
    });
    return engine;
  }, [hangUp]);

  // The stream: this tab's calls and the signals addressed to it.
  const streamOn = config.state === 'ready' && config.config.enabled;
  useEffect(() => {
    if (!streamOn) return;
    const controller = new AbortController();
    void callsApi.runCallStream(clientId, {
      onState: () => undefined,
      onCall: (call) => {
        const current = activeRef.current;
        if (current && current.call.id === call.id) {
          if (call.status === 'ended') { finish(call); return; }
          const next = { ...current, call };
          if (current.role === 'caller' && call.status === 'accepted' && !current.offered && current.engine) {
            next.offered = true;
            void current.engine.sendOffer().catch(() => void hangUp('failed'));
          }
          activeRef.current = next;
          setActive(next);
          return;
        }
        const ringing = incomingRef.current;
        if (ringing && ringing.id === call.id) {
          if (call.status !== 'ringing' || call.answered_elsewhere) {
            setIncoming(null);
            if (call.answered_elsewhere) setNotice(t('appCalls.answeredElsewhere'));
            bump();
          }
          return;
        }
        if (call.direction === 'incoming' && call.status === 'ringing' && !call.answered_elsewhere) {
          if (current || ringing) { void callsApi.endCall(call.id, 'busy').catch(() => undefined); return; }
          setEnded(null);
          setIncoming(call);
          if (document.hidden && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
            try { new Notification(t('appCalls.notify.title'), { body: t('appCalls.notify.body', { name: call.peer_name }), tag: `app-call-${call.id}` }); } catch { /* optional */ }
          }
        }
        if (call.status === 'ended') bump();
      },
      onSignal: (signal) => {
        const engine = activeRef.current?.call.id === signal.call_id ? activeRef.current.engine : null;
        if (engine) void engine.handleSignal(signal).catch(() => void hangUp('failed'));
        else queued.current.set(signal.call_id, [...(queued.current.get(signal.call_id) ?? []), signal]);
      },
    }, controller.signal);
    return () => controller.abort();
  }, [streamOn, clientId, finish, hangUp, bump]);

  const unavailable = useMemo(() => {
    if (config.state === 'loading') return null;
    if (config.state === 'denied') return t('appCalls.denied');
    if (config.state === 'error') return t('appCalls.configFailed', { message: config.message });
    if (!config.config.enabled) return t('appCalls.off');
    if (!online) return t('appCalls.offline');
    return null;
  }, [config, online]);

  const start = useCallback((person: Person, media: AppCallMedia) => {
    if (activeRef.current || preparing) { setNotice(t('appCalls.alreadyInCall')); return; }
    if (unavailable) { setNotice(unavailable); return; }
    setEnded(null); setNotice(null);
    setPreparing({ person, media });
    void (async () => {
      let local: MediaStream | null = null;
      try { local = await openLocalMedia(media); }
      catch (error) { setPreparing(null); setNotice(mediaMessage(error)); return; }
      try {
        const call = await callsApi.placeCall(person.user_id, media, clientId);
        if (call.status === 'ended') { for (const track of local.getTracks()) track.stop(); setPreparing(null); finish(call); return; }
        const engine = await engineFor(call, 'caller', local);
        const next: Active = { call, role: 'caller', engine, offered: false };
        activeRef.current = next;
        setActive(next); setPreparing(null); bump();
      } catch (error) {
        for (const track of local.getTracks()) track.stop();
        setPreparing(null);
        setNotice(t('appCalls.startFailed', { message: describeError(error) }));
      }
    })();
  }, [preparing, unavailable, clientId, engineFor, finish, bump]);

  const answer = useCallback(async (withVideo: boolean) => {
    const call = incomingRef.current;
    if (!call) return;
    setIncoming(null);
    let local: MediaStream;
    try { local = await openLocalMedia(withVideo && call.media === 'video' ? 'video' : 'audio'); }
    catch (error) {
      setNotice(mediaMessage(error));
      void callsApi.endCall(call.id, 'failed').then(bump, bump);
      return;
    }
    try {
      const engine = await engineFor(call, 'callee', local);
      const pending: Active = { call, role: 'callee', engine, offered: true };
      activeRef.current = pending;
      setActive(pending);
      const accepted = await callsApi.acceptCall(call.id, clientId);
      const next = { ...pending, call: accepted };
      activeRef.current = next;
      setActive(next);
      for (const signal of queued.current.get(call.id) ?? []) await engine.handleSignal(signal);
      queued.current.delete(call.id);
      bump();
    } catch (error) {
      teardown();
      setNotice(t('appCalls.answerFailed', { message: describeError(error) }));
      bump();
    }
  }, [engineFor, clientId, teardown, bump]);

  const decline = useCallback(() => {
    const call = incomingRef.current;
    if (!call) return;
    setIncoming(null);
    void callsApi.endCall(call.id, 'decline').then(bump, bump);
  }, [bump]);

  const tone = incoming ? 'incoming' : active?.call.status === 'ringing' ? 'ringback' : null;
  useTone(tone);

  const value = useMemo<CallsContext>(() => ({ config, unavailable, inCall: Boolean(active || preparing), start, revision }), [config, unavailable, active, preparing, start, revision]);

  return <Context.Provider value={value}>
    {children}
    {notice && <div className="appCallNotice" role="status"><span>{notice}</span><button type="button" className="iconButton small" aria-label={t('appCalls.close')} title={t('appCalls.close')} onClick={() => setNotice(null)}><X aria-hidden="true" /></button></div>}
    {preparing && <CallScreen phase="preparing" name={preparing.person.display_name} media={preparing.media} onHangUp={() => undefined} />}
    {incoming && !active && <IncomingScreen call={incoming} onAnswer={answer} onDecline={decline} />}
    {active && <CallScreen
      phase={phaseFor(active.call, { connected: connection === 'connected', reconnecting: connection === 'reconnecting' })}
      call={active.call} name={active.call.peer_name} media={active.call.media}
      engine={active.engine} remote={remote} onHangUp={() => void hangUp()} />}
    {ended && !active && !incoming && <EndedScreen ended={ended} onClose={() => setEnded(null)} onCallAgain={(media) => { setEnded(null); start(ended.person, media); }} />}
  </Context.Provider>;
}

function Avatar({ name }: { name: string }) {
  return <span className="appCallAvatar" aria-hidden="true">{initialsOf(name)}</span>;
}

function IncomingScreen({ call, onAnswer, onDecline }: { call: AppCall; onAnswer(withVideo: boolean): void; onDecline(): void }) {
  const panel = useModal(onDecline);
  const titleId = useId();
  return <div className="appCallLayer">
    <section className="surface appCallPanel incoming" role="alertdialog" aria-modal="true" aria-labelledby={titleId} ref={panel as never}>
      <Avatar name={call.peer_name} />
      <h2 id={titleId}><bdi>{call.peer_name}</bdi></h2>
      <p className="appCallStatus">{t(call.media === 'video' ? 'appCalls.videoCall' : 'appCalls.voiceCall')} · {t('appCalls.phase.incoming')}</p>
      <div className="appCallControls">
        <button type="button" className="appCallButton accept" onClick={() => onAnswer(false)} data-autofocus><Phone aria-hidden="true" /><span>{t('appCalls.answer')}</span></button>
        {call.media === 'video' && <button type="button" className="appCallButton accept" onClick={() => onAnswer(true)}><Video aria-hidden="true" /><span>{t('appCalls.answerVideo')}</span></button>}
        <button type="button" className="appCallButton hangup" onClick={onDecline}><PhoneOff aria-hidden="true" /><span>{t('appCalls.decline')}</span></button>
      </div>
    </section>
  </div>;
}

function CallScreen({ phase, call, name, media, engine, remote, onHangUp }: {
  phase: CallPhase; call?: AppCall; name: string; media: AppCallMedia; engine?: AppCallEngine | null; remote?: MediaStream | null; onHangUp(): void;
}) {
  const [muted, setMuted] = useState(false);
  const [speaker, setSpeaker] = useState(true);
  const [camera, setCamera] = useState(media === 'video');
  const [compact, setCompact] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const audio = useRef<HTMLAudioElement>(null);
  const remoteVideo = useRef<HTMLVideoElement>(null);
  const localVideo = useRef<HTMLVideoElement>(null);
  const titleId = useId();
  const panel = useModal(() => setCompact(true));
  useEffect(() => { if (phase !== 'connected') return; const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, [phase]);
  useEffect(() => { engine?.setMuted(muted); }, [engine, muted]);
  useEffect(() => { engine?.setCamera(camera); }, [engine, camera]);
  useEffect(() => { if (audio.current) audio.current.muted = !speaker; }, [speaker]);
  useEffect(() => {
    if (audio.current && remote && audio.current.srcObject !== remote) { audio.current.srcObject = remote; void audio.current.play().catch(() => undefined); }
    if (remoteVideo.current && remote && remoteVideo.current.srcObject !== remote) remoteVideo.current.srcObject = remote;
  }, [remote, compact]);
  useEffect(() => { if (localVideo.current && engine && localVideo.current.srcObject !== engine.local) localVideo.current.srcObject = engine.local; }, [engine, compact]);
  const status = phase === 'connected' && call ? formatDuration(elapsedSeconds(call, now)) : t(`appCalls.phase.${phase === 'idle' || phase === 'ended' || phase === 'incoming' ? 'connecting' : phase}` as MessageKey);
  const video = media === 'video';
  const controls = <div className="appCallControls">
    <button type="button" className="appCallButton" aria-pressed={muted} onClick={() => setMuted(!muted)} disabled={!engine}>{muted ? <MicOff aria-hidden="true" /> : <Mic aria-hidden="true" />}<span>{muted ? t('appCalls.unmute') : t('appCalls.mute')}</span></button>
    <button type="button" className="appCallButton" aria-pressed={speaker} onClick={() => setSpeaker(!speaker)} disabled={!engine}>{speaker ? <Volume2 aria-hidden="true" /> : <VolumeX aria-hidden="true" />}<span>{speaker ? t('appCalls.speakerOff') : t('appCalls.speakerOn')}</span></button>
    {video && <button type="button" className="appCallButton" aria-pressed={!camera} onClick={() => setCamera(!camera)} disabled={!engine}>{camera ? <CameraOff aria-hidden="true" /> : <Camera aria-hidden="true" />}<span>{camera ? t('appCalls.cameraOff') : t('appCalls.cameraOn')}</span></button>}
    <button type="button" className="appCallButton hangup" onClick={onHangUp} disabled={phase === 'preparing'}><PhoneOff aria-hidden="true" /><span>{t('appCalls.hangUp')}</span></button>
  </div>;
  return <>
    <audio ref={audio} autoPlay hidden />
    {compact
      ? <div className="appCallPill" role="region" aria-label={t('appCalls.screen', { name })}>
          <span className="appCallPillText"><bdi>{name}</bdi> · <span aria-live="polite">{status}</span></span>
          <button type="button" className="iconButton small" aria-label={t('appCalls.screen', { name })} title={t('appCalls.screen', { name })} onClick={() => setCompact(false)}><Maximize2 aria-hidden="true" /></button>
          <button type="button" className="appCallButton hangup small" onClick={onHangUp}><PhoneOff aria-hidden="true" /><span>{t('appCalls.hangUp')}</span></button>
        </div>
      : <div className="appCallLayer">
          <section className={`surface appCallPanel ${video ? 'withVideo' : ''}`} role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel as never}>
            <button type="button" className="iconButton small appCallMinimize" aria-label={t('appCalls.minimise')} title={t('appCalls.minimise')} onClick={() => setCompact(true)}><Minimize2 aria-hidden="true" /></button>
            {video
              ? <div className="appCallVideos">
                  <video ref={remoteVideo} className="appCallRemote" autoPlay playsInline muted aria-label={t('appCalls.remoteVideo', { name })} />
                  <video ref={localVideo} className="appCallLocal" autoPlay playsInline muted aria-label={t('appCalls.localVideo')} />
                </div>
              : <Avatar name={name} />}
            <h2 id={titleId}><bdi>{name}</bdi></h2>
            <p className="appCallStatus" aria-live="polite">{t(video ? 'appCalls.videoCall' : 'appCalls.voiceCall')} · <span data-testid="call-status">{status}</span></p>
            {controls}
          </section>
        </div>}
  </>;
}

function EndedScreen({ ended, onClose, onCallAgain }: { ended: NonNullable<Ended>; onClose(): void; onCallAgain(media: AppCallMedia): void }) {
  const panel = useModal(onClose);
  const titleId = useId();
  const word = outcomeWord(ended.call);
  return <div className="appCallLayer">
    <section className="surface appCallPanel" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel as never}>
      <Avatar name={ended.person.display_name} />
      <h2 id={titleId}><bdi>{ended.person.display_name}</bdi></h2>
      <p className="appCallStatus" role="status">{word ? t(`appCalls.word.${word}` as MessageKey) : t('appCalls.word.completed')}</p>
      {ended.message && <p className="appCallDetail">{ended.message}</p>}
      <div className="appCallControls">
        <button type="button" className="appCallButton accept" onClick={() => onCallAgain(ended.call.media)}><Phone aria-hidden="true" /><span>{t('appCalls.callAgain')}</span></button>
        <button type="button" className="appCallButton" onClick={onClose} data-autofocus><X aria-hidden="true" /><span>{t('appCalls.close')}</span></button>
      </div>
    </section>
  </div>;
}

/** Call and Video buttons for one teammate (Team, People). */
export function CallButtons({ person, compact = false }: { person: Person; compact?: boolean }) {
  const calls = useAppCalls();
  const hintId = useId();
  if (!calls || calls.config.state === 'loading') return null;
  const reason = calls.unavailable;
  const video = calls.config.state === 'ready' && calls.config.config.video_enabled;
  return <span className="callButtons">
    <button type="button" className="secondary" disabled={Boolean(reason) || calls.inCall} aria-describedby={reason ? hintId : undefined} aria-label={t('appCalls.callPerson', { name: person.display_name })} onClick={() => calls.start(person, 'audio')}><Phone aria-hidden="true" />{!compact && t('appCalls.call')}</button>
    {video && <button type="button" className="secondary" disabled={Boolean(reason) || calls.inCall} aria-describedby={reason ? hintId : undefined} aria-label={t('appCalls.videoPerson', { name: person.display_name })} onClick={() => calls.start(person, 'video')}><Video aria-hidden="true" />{!compact && t('appCalls.video')}</button>}
    {reason && <span id={hintId} className="srOnly">{reason}</span>}
  </span>;
}
