// In-app calls between teammates (docs/in-app-calls.md): the provider that keeps this tab's call
// stream open, rings for incoming calls and runs the WebRTC sessions, and the call screen
// (incoming, ringing, connecting, connected with timer, mute, speaker, camera, video, screen
// sharing, adding people, hang up, ended). A group call (up to four people) is a mesh: one
// connection per other person, all sharing this tab's microphone and camera.
// Calls off on the server, a refused microphone, offline and failed calls each say what happened.
import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { Camera, CameraOff, Mic, MicOff, Minimize2, Maximize2, MonitorUp, MonitorX, Phone, PhoneOff, UserPlus, Users, Video, Volume2, VolumeX, X } from 'lucide-react';
import type { AppCall, AppCallConfig, AppCallMedia, AppCallRoom, AppCallSignal } from '@anum/contracts';
import * as callsApi from './lib/appCallsApi';
import { AppCallEngine, openLocalMedia, type PeerMediaState } from './lib/appCallEngine';
import {
  activeOthers, canShareScreen, elapsedSeconds, formatDuration, freeSlots, initialsOf, mediaProblem, newClientId, outcomeWord, phaseFor, type CallPhase,
} from './lib/appCalls';
import { describeError, isPermissionDenied } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { useModal } from './Dialog';
import { CallPeoplePicker } from './CallPeoplePicker';
import './appCalls.css';

export type CallConfigState =
  | { state: 'loading' } | { state: 'ready'; config: AppCallConfig } | { state: 'denied' } | { state: 'error'; message: string };

export interface Person { user_id: string; display_name: string }

interface CallsContext {
  config: CallConfigState;
  /** Why a call cannot be placed right now, or null. */
  unavailable: string | null;
  inCall: boolean;
  start(person: Person, media: AppCallMedia): void;
  /** Rings two or three teammates at once (a group call of up to four people). */
  startGroup(people: Person[], media: AppCallMedia): void;
  /** Bumped whenever a call starts, changes or ends, so lists can reload. */
  revision: number;
}

const Context = createContext<CallsContext | null>(null);
export function useAppCalls(): CallsContext | null { return useContext(Context); }

type Connection = 'connecting' | 'connected' | 'reconnecting' | 'failed';

/** One connection of the call: with the one other person, or with one person of a group call. */
interface Link {
  call: AppCall;
  role: 'caller' | 'callee';
  engine: AppCallEngine | null;
  offered: boolean;
  remote: MediaStream | null;
  connection: Connection;
  peer: PeerMediaState | null;
}

interface Session {
  roomId: string | null;
  room: AppCallRoom | null;
  local: MediaStream;
  links: Map<string, Link>;
  /** The call that started this session (the one hung up in a call between two people). */
  primaryId: string | null;
  title: Person | null;
  media: AppCallMedia;
  screen: MediaStreamTrack | null;
  muted: boolean;
  camera: boolean;
}

type Ended = { call: AppCall | null; message: string | null; person: Person | null; group: string[] | null } | null;

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

const liveLinks = (session: Session) => [...session.links.values()].filter((link) => link.call.status !== 'ended');

export function AppCallProvider({ children }: { children: ReactNode }) {
  const clientId = useRef(newClientId()).current;
  const [config, setConfig] = useState<CallConfigState>({ state: 'loading' });
  const [online, setOnline] = useState(() => typeof navigator === 'undefined' || navigator.onLine !== false);
  const [, setTick] = useState(0);
  const [preparing, setPreparing] = useState<{ title: string; media: AppCallMedia } | null>(null);
  const [incoming, setIncoming] = useState<AppCall | null>(null);
  const [ended, setEnded] = useState<Ended>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const sessionRef = useRef<Session | null>(null);
  const incomingRef = useRef<AppCall | null>(null);
  const queued = useRef(new Map<string, AppCallSignal[]>());
  // The latest frame of every call and group this tab has seen, so a call that arrives before the
  // request that started it has answered is picked up once the session knows its group.
  const frames = useRef(new Map<string, AppCall>());
  const rooms = useRef(new Map<string, AppCallRoom>());
  incomingRef.current = incoming;
  const rerender = useCallback(() => setTick((n) => n + 1), []);
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
    const session = sessionRef.current;
    sessionRef.current = null;
    if (session) {
      for (const link of session.links.values()) link.engine?.close();
      for (const track of session.local.getTracks()) track.stop();
      session.screen?.stop();
    }
    rerender();
  }, [rerender]);

  const finish = useCallback((last: AppCall | null) => {
    const session = sessionRef.current;
    if (session?.roomId) {
      const names = [...new Set([...session.links.values()].map((link) => link.call.peer_name))];
      void callsApi.leaveGroupCall(session.roomId).catch(() => undefined);
      teardown();
      setEnded({ call: null, message: null, person: null, group: names });
    } else {
      const call = last ?? (session?.primaryId ? session.links.get(session.primaryId)?.call ?? null : null);
      teardown();
      if (call) setEnded({ call, message: endMessage(call), person: { user_id: call.peer_user_id, display_name: call.peer_name }, group: null });
    }
    bump();
  }, [teardown, bump]);

  const hangUp = useCallback(async (reason: 'hangup' | 'failed' = 'hangup') => {
    const session = sessionRef.current;
    if (!session) return;
    if (session.roomId) {
      try { await callsApi.leaveGroupCall(session.roomId, reason); } catch { /* the server settles silent members */ }
      finish(null);
      return;
    }
    const primary = session.primaryId ? session.links.get(session.primaryId) : null;
    if (!primary) { finish(null); return; }
    try { finish(await callsApi.endCall(primary.call.id, reason)); }
    catch { finish({ ...primary.call, status: 'ended', outcome: reason === 'failed' ? 'failed' : primary.call.outcome }); }
  }, [finish]);

  /** Sends the right picture on one connection: the shared screen, else the camera, else nothing. */
  const applyVideo = useCallback((link: Link) => {
    const session = sessionRef.current;
    if (!session || !link.engine || !link.engine.canSendVideo()) return;
    const camera = session.local.getVideoTracks()[0] ?? null;
    const track = session.screen ?? (camera && session.camera ? camera : null);
    void link.engine.setVideoTrack(track, { camera: Boolean(camera && session.camera), screen: Boolean(session.screen) }).catch(() => undefined);
  }, []);

  const createEngine = useCallback(async (link: Link) => {
    const session = sessionRef.current;
    if (!session || link.engine) return;
    const servers = await callsApi.getIceServers();
    if (sessionRef.current !== session || link.engine) return;
    const callId = link.call.id;
    link.engine = new AppCallEngine(link.role, session.local, servers, {
      sendDescription: (kind, sdp) => callsApi.sendDescription(callId, kind, sdp),
      sendCandidates: (candidates) => callsApi.sendCandidates(callId, candidates),
      heartbeat: () => callsApi.sendHeartbeat(callId),
    }, {
      onRemoteStream: (stream) => { link.remote = stream; rerender(); },
      onPeerState: (state) => { link.peer = state; rerender(); },
      onConnection: (state) => {
        link.connection = state;
        rerender();
        if (state !== 'failed') return;
        // In a group call one lost connection ends only that pair; otherwise the call.
        if (sessionRef.current?.roomId) void callsApi.endCall(callId, 'failed').catch(() => undefined);
        else void hangUp('failed');
      },
    });
    link.engine.setMuted(session.muted);
    for (const signal of queued.current.get(callId) ?? []) await link.engine.handleSignal(signal).catch(() => undefined);
    queued.current.delete(callId);
    rerender();
  }, [rerender, hangUp]);

  const offer = useCallback(async (link: Link) => {
    if (link.offered || link.role !== 'caller' || link.call.status !== 'accepted') return;
    link.offered = true;
    await createEngine(link);
    if (!link.engine) return;
    applyVideo(link);
    await link.engine.sendOffer().catch(() => {
      if (sessionRef.current?.roomId) void callsApi.endCall(link.call.id, 'failed').catch(() => undefined);
      else void hangUp('failed');
    });
  }, [createEngine, applyVideo, hangUp]);

  const addLink = useCallback((call: AppCall) => {
    const session = sessionRef.current;
    if (!session || session.links.has(call.id)) return;
    const link: Link = { call, role: call.direction === 'outgoing' ? 'caller' : 'callee', engine: null, offered: false, remote: null, connection: 'connecting', peer: null };
    session.links.set(call.id, link);
    if (call.status === 'accepted') {
      if (link.role === 'caller') void offer(link);
      else void createEngine(link);
    }
    rerender();
  }, [offer, createEngine, rerender]);

  /** Picks up the calls of the session's group that arrived before the session knew the group. */
  const adoptRoom = useCallback((roomId: string) => {
    const session = sessionRef.current;
    if (!session) return;
    session.roomId = roomId;
    session.room = rooms.current.get(roomId) ?? session.room;
    for (const call of frames.current.values()) {
      if (call.room_id === roomId && call.on_this_device && call.status !== 'ended' && !session.links.has(call.id)) addLink(call);
    }
    rerender();
  }, [addLink, rerender]);

  /** In a group call, a ring you placed that ended says why (the others stay in the call). */
  const noteRingEnd = useCallback((call: AppCall) => {
    if (call.direction !== 'outgoing') return;
    const word = outcomeWord(call);
    if (word === 'declinedByPeer') setNotice(t('appCalls.ended.declinedByPeer', { name: call.peer_name }));
    else if (word === 'busyPeer') setNotice(t('appCalls.ended.busyPeer', { name: call.peer_name }));
    else if (word === 'noAnswer') setNotice(t('appCalls.ended.noAnswer', { name: call.peer_name }));
  }, []);

  const updateLink = useCallback((link: Link, call: AppCall) => {
    const session = sessionRef.current;
    if (!session) return;
    link.call = call;
    if (call.room_id && session.roomId !== call.room_id) adoptRoom(call.room_id);
    if (call.status === 'ended') {
      link.engine?.close();
      link.engine = null;
      if (!liveLinks(session).length) { finish(session.roomId ? null : call); return; }
      if (session.roomId) noteRingEnd(call);
      bump();
    } else if (link.role === 'caller' && call.status === 'accepted' && !link.offered) {
      void offer(link);
    }
    rerender();
  }, [adoptRoom, finish, offer, noteRingEnd, bump, rerender]);

  // The stream: this tab's calls, groups and the signals addressed to it.
  const streamOn = config.state === 'ready' && config.config.enabled;
  useEffect(() => {
    if (!streamOn) return;
    const controller = new AbortController();
    void callsApi.runCallStream(clientId, {
      onState: () => undefined,
      onRoom: (room) => {
        rooms.current.set(room.id, room);
        const session = sessionRef.current;
        if (session && session.roomId === room.id) { session.room = room; rerender(); }
        else if (incomingRef.current?.room_id === room.id) rerender();
      },
      onCall: (call) => {
        frames.current.set(call.id, call);
        const session = sessionRef.current;
        if (session) {
          const link = session.links.get(call.id);
          if (link) { updateLink(link, call); return; }
          if (call.room_id && call.room_id === session.roomId && call.on_this_device) {
            // A ring that ended before this tab saw it ring still says why.
            if (call.status === 'ended') noteRingEnd(call);
            else addLink(call);
            return;
          }
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
          if (session || ringing) { void callsApi.endCall(call.id, 'busy').catch(() => undefined); return; }
          setEnded(null);
          setIncoming(call);
          if (document.hidden && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
            try { new Notification(t('appCalls.notify.title'), { body: t('appCalls.notify.body', { name: call.peer_name }), tag: `app-call-${call.id}` }); } catch { /* optional */ }
          }
        }
        if (call.status === 'ended') bump();
      },
      onSignal: (signal) => {
        const link = sessionRef.current?.links.get(signal.call_id);
        if (link?.engine) {
          void link.engine.handleSignal(signal).then(() => { if (signal.kind === 'offer') applyVideo(link); }).catch(() => {
            if (sessionRef.current?.roomId) void callsApi.endCall(signal.call_id, 'failed').catch(() => undefined);
            else void hangUp('failed');
          });
        } else queued.current.set(signal.call_id, [...(queued.current.get(signal.call_id) ?? []), signal]);
      },
    }, controller.signal);
    return () => controller.abort();
  }, [streamOn, clientId, updateLink, addLink, noteRingEnd, applyVideo, hangUp, bump, rerender]);

  const unavailable = useMemo(() => {
    if (config.state === 'loading') return null;
    if (config.state === 'denied') return t('appCalls.denied');
    if (config.state === 'error') return t('appCalls.configFailed', { message: config.message });
    if (!config.config.enabled) return t('appCalls.off');
    if (!online) return t('appCalls.offline');
    return null;
  }, [config, online]);

  const newSession = useCallback((local: MediaStream, media: AppCallMedia, title: Person | null, roomId: string | null): Session => ({
    roomId, room: roomId ? rooms.current.get(roomId) ?? null : null, local, links: new Map(), primaryId: null, title, media, screen: null, muted: false,
    camera: local.getVideoTracks().length > 0,
  }), []);

  const begin = useCallback((title: string, media: AppCallMedia, place: (local: MediaStream) => Promise<void>) => {
    if (sessionRef.current || preparing) { setNotice(t('appCalls.alreadyInCall')); return; }
    if (unavailable) { setNotice(unavailable); return; }
    setEnded(null); setNotice(null);
    setPreparing({ title, media });
    void (async () => {
      let local: MediaStream | null = null;
      try { local = await openLocalMedia(media); }
      catch (error) { setPreparing(null); setNotice(mediaMessage(error)); return; }
      try { await place(local); }
      catch (error) {
        for (const track of local.getTracks()) track.stop();
        sessionRef.current = null;
        setNotice(t('appCalls.startFailed', { message: describeError(error) }));
      } finally { setPreparing(null); }
    })();
  }, [preparing, unavailable]);

  const start = useCallback((person: Person, media: AppCallMedia) => {
    begin(person.display_name, media, async (local) => {
      const call = await callsApi.placeCall(person.user_id, media, clientId);
      frames.current.set(call.id, call);
      if (call.status === 'ended') { for (const track of local.getTracks()) track.stop(); setEnded({ call, message: endMessage(call), person, group: null }); bump(); return; }
      const session = newSession(local, media, person, null);
      session.primaryId = call.id;
      sessionRef.current = session;
      addLink(frames.current.get(call.id) ?? call);
      bump();
    });
  }, [begin, clientId, newSession, addLink, bump]);

  const startGroup = useCallback((people: Person[], media: AppCallMedia) => {
    begin(t('appCalls.group.title'), media, async (local) => {
      const room = await callsApi.placeGroupCall(people.map((p) => p.user_id), media, clientId);
      rooms.current.set(room.id, room);
      sessionRef.current = newSession(local, media, null, room.id);
      adoptRoom(room.id);
      if (room.status === 'ended') { setNotice(t('appCalls.group.allBusy')); finish(null); return; }
      bump();
    });
  }, [begin, clientId, newSession, adoptRoom, finish, bump]);

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
    const session = newSession(local, call.media, { user_id: call.peer_user_id, display_name: call.peer_name }, null);
    session.primaryId = call.id;
    sessionRef.current = session;
    const link: Link = { call, role: 'callee', engine: null, offered: true, remote: null, connection: 'connecting', peer: null };
    session.links.set(call.id, link);
    try {
      await createEngine(link);
      const accepted = await callsApi.acceptCall(call.id, clientId);
      link.call = accepted;
      if (accepted.room_id) adoptRoom(accepted.room_id);
      rerender();
      bump();
    } catch (error) {
      teardown();
      setNotice(t('appCalls.answerFailed', { message: describeError(error) }));
      bump();
    }
  }, [newSession, createEngine, clientId, adoptRoom, teardown, rerender, bump]);

  const decline = useCallback(() => {
    const call = incomingRef.current;
    if (!call) return;
    setIncoming(null);
    void callsApi.endCall(call.id, 'decline').then(bump, bump);
  }, [bump]);

  const connectedLink = useCallback(() => {
    const session = sessionRef.current;
    return session ? liveLinks(session).find((link) => link.call.status === 'accepted') ?? null : null;
  }, []);

  const invite = useCallback(async (person: Person) => {
    const link = connectedLink();
    if (!link) return;
    try {
      const room = await callsApi.inviteToCall(link.call.id, person.user_id, clientId);
      rooms.current.set(room.id, room);
      adoptRoom(room.id);
    } catch (error) {
      setNotice(t('appCalls.group.addFailed', { message: describeError(error) }));
    }
  }, [connectedLink, clientId, adoptRoom]);

  const setMuted = useCallback((muted: boolean) => {
    const session = sessionRef.current;
    if (!session) return;
    session.muted = muted;
    for (const link of session.links.values()) link.engine?.setMuted(muted);
    rerender();
  }, [rerender]);

  const setCamera = useCallback((on: boolean) => {
    const session = sessionRef.current;
    if (!session) return;
    session.camera = on;
    for (const link of session.links.values()) { link.engine?.setCamera(on); applyVideo(link); }
    rerender();
  }, [applyVideo, rerender]);

  const turnOnVideo = useCallback(async () => {
    const session = sessionRef.current;
    const link = connectedLink();
    if (!session || !link) return;
    try { await callsApi.turnOnVideo(link.call.id, 'camera'); }
    catch (error) { setNotice(t('appCalls.video.refused', { message: describeError(error) })); return; }
    let camera: MediaStreamTrack;
    try { [camera] = (await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' } })).getVideoTracks(); }
    catch (error) { setNotice(mediaMessage(error)); return; }
    if (sessionRef.current !== session) { camera.stop(); return; }
    session.local.addTrack(camera);
    session.camera = true;
    session.media = 'video';
    for (const each of session.links.values()) applyVideo(each);
    if (liveLinks(session).some((each) => each.engine && !each.engine.canSendVideo())) setNotice(t('appCalls.video.peerCannot'));
    rerender();
  }, [connectedLink, applyVideo, rerender]);

  const stopShare = useCallback(() => {
    const session = sessionRef.current;
    if (!session?.screen) return;
    session.screen.onended = null;
    session.screen.stop();
    session.screen = null;
    for (const link of session.links.values()) applyVideo(link);
    rerender();
  }, [applyVideo, rerender]);

  const shareScreen = useCallback(async () => {
    const session = sessionRef.current;
    const link = connectedLink();
    if (!session || !link || session.screen) return;
    let screen: MediaStreamTrack;
    try { [screen] = (await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false })).getVideoTracks(); }
    catch (error) {
      // Closing the browser's picker is not an error worth a message.
      if (error instanceof DOMException && error.name === 'NotAllowedError') return;
      setNotice(t('appCalls.screen.failed', { message: error instanceof Error ? error.message : String(error) }));
      return;
    }
    try { await callsApi.turnOnVideo(link.call.id, 'screen'); }
    catch (error) { screen.stop(); setNotice(t('appCalls.video.refused', { message: describeError(error) })); return; }
    if (sessionRef.current !== session) { screen.stop(); return; }
    session.screen = screen;
    screen.onended = stopShare;
    for (const each of session.links.values()) applyVideo(each);
    if (liveLinks(session).some((each) => each.engine && !each.engine.canSendVideo())) setNotice(t('appCalls.video.peerCannot'));
    rerender();
  }, [connectedLink, applyVideo, stopShare, rerender]);

  const session = sessionRef.current;
  const live = session ? liveLinks(session) : [];
  const ringingOut = live.some((link) => link.call.status === 'ringing') && !live.some((link) => link.call.status === 'accepted');
  useTone(incoming ? 'incoming' : ringingOut ? 'ringback' : null);

  const inCall = Boolean(session || preparing);
  const value = useMemo<CallsContext>(
    () => ({ config, unavailable, inCall, start, startGroup, revision }),
    [config, unavailable, inCall, start, startGroup, revision],
  );
  const videoEnabled = config.state === 'ready' && config.config.video_enabled;

  return <Context.Provider value={value}>
    {children}
    {notice && <div className="appCallNotice" role="status"><span>{notice}</span><button type="button" className="iconButton small" aria-label={t('appCalls.close')} title={t('appCalls.close')} onClick={() => setNotice(null)}><X aria-hidden="true" /></button></div>}
    {preparing && <PreparingScreen title={preparing.title} media={preparing.media} />}
    {incoming && !session && <IncomingScreen call={incoming} room={incoming.room_id ? rooms.current.get(incoming.room_id) ?? null : null} onAnswer={answer} onDecline={decline} />}
    {session && <CallScreen
      session={session} videoEnabled={videoEnabled}
      onHangUp={() => void hangUp()} onMute={setMuted} onCamera={setCamera} onVideo={() => void turnOnVideo()}
      onShare={() => void shareScreen()} onStopShare={stopShare} onInvite={(person) => void invite(person)} />}
    {ended && !session && !incoming && <EndedScreen ended={ended} onClose={() => setEnded(null)} onCallAgain={(person, media) => { setEnded(null); start(person, media); }} />}
  </Context.Provider>;
}

function Avatar({ name }: { name: string }) {
  return <span className="appCallAvatar" aria-hidden="true">{initialsOf(name)}</span>;
}

function PreparingScreen({ title, media }: { title: string; media: AppCallMedia }) {
  const titleId = useId();
  const panel = useModal(() => undefined);
  return <div className="appCallLayer">
    <section className="surface appCallPanel" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel as never}>
      <Avatar name={title} />
      <h2 id={titleId}><bdi>{title}</bdi></h2>
      <p className="appCallStatus" aria-live="polite">{t(media === 'video' ? 'appCalls.videoCall' : 'appCalls.voiceCall')} · <span data-testid="call-status">{t('appCalls.phase.preparing')}</span></p>
      <div className="appCallControls">
        <button type="button" className="appCallButton hangup" disabled><PhoneOff aria-hidden="true" /><span>{t('appCalls.hangUp')}</span></button>
      </div>
    </section>
  </div>;
}

function IncomingScreen({ call, room, onAnswer, onDecline }: { call: AppCall; room: AppCallRoom | null; onAnswer(withVideo: boolean): void; onDecline(): void }) {
  const panel = useModal(onDecline);
  const titleId = useId();
  const others = activeOthers(room).filter((m) => m.user_id !== call.peer_user_id).map((m) => m.name);
  return <div className="appCallLayer">
    <section className="surface appCallPanel incoming" role="alertdialog" aria-modal="true" aria-labelledby={titleId} ref={panel as never}>
      <Avatar name={call.peer_name} />
      <h2 id={titleId}><bdi>{call.peer_name}</bdi></h2>
      <p className="appCallStatus">{t(call.media === 'video' ? 'appCalls.videoCall' : 'appCalls.voiceCall')} · {t(call.room_id ? 'appCalls.group.incoming' : 'appCalls.phase.incoming')}</p>
      {others.length > 0 && <p className="appCallDetail">{t('appCalls.group.with', { names: others.join(', ') })}</p>}
      <div className="appCallControls">
        <button type="button" className="appCallButton accept" onClick={() => onAnswer(false)} data-autofocus><Phone aria-hidden="true" /><span>{t('appCalls.answer')}</span></button>
        {call.media === 'video' && <button type="button" className="appCallButton accept" onClick={() => onAnswer(true)}><Video aria-hidden="true" /><span>{t('appCalls.answerVideo')}</span></button>}
        <button type="button" className="appCallButton hangup" onClick={onDecline}><PhoneOff aria-hidden="true" /><span>{t('appCalls.decline')}</span></button>
      </div>
    </section>
  </div>;
}

function linkPhase(link: Link): CallPhase {
  const phase = phaseFor(link.call, { connected: link.connection === 'connected', reconnecting: link.connection === 'reconnecting' });
  return phase === 'idle' || phase === 'ended' || phase === 'incoming' ? 'connecting' : phase;
}

/** Whether the other person's camera or screen is coming in on this connection. */
function peerVideo(link: Link): boolean {
  if (!link.remote?.getVideoTracks().length) return false;
  if (link.peer) return link.peer.camera || link.peer.screen;
  return link.call.media === 'video';
}

function RemoteAudio({ stream, speaker }: { stream: MediaStream | null; speaker: boolean }) {
  const audio = useRef<HTMLAudioElement>(null);
  useEffect(() => {
    if (audio.current && stream && audio.current.srcObject !== stream) { audio.current.srcObject = stream; void audio.current.play().catch(() => undefined); }
  }, [stream]);
  useEffect(() => { if (audio.current) audio.current.muted = !speaker; }, [speaker]);
  return <audio ref={audio} autoPlay hidden />;
}

function VideoOf({ stream, label, className, mirrored }: { stream: MediaStream | null; label: string; className: string; mirrored?: boolean }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => { if (ref.current && ref.current.srcObject !== stream) ref.current.srcObject = stream; }, [stream]);
  return <video ref={ref} className={`${className}${mirrored ? ' mirrored' : ''}`} autoPlay playsInline muted aria-label={label} />;
}

function CallScreen({ session, videoEnabled, onHangUp, onMute, onCamera, onVideo, onShare, onStopShare, onInvite }: {
  session: Session; videoEnabled: boolean; onHangUp(): void; onMute(muted: boolean): void; onCamera(on: boolean): void; onVideo(): void;
  onShare(): void; onStopShare(): void; onInvite(person: Person): void;
}) {
  const [speaker, setSpeaker] = useState(true);
  const [compact, setCompact] = useState(false);
  const [picking, setPicking] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const titleId = useId();
  const links = liveLinks(session);
  const group = Boolean(session.roomId);
  const connected = links.filter((link) => link.connection === 'connected' && link.call.status === 'accepted');
  const first = connected[0] ?? links[0] ?? null;
  useEffect(() => { if (!connected.length) return; const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, [connected.length]);
  const answeredAt = connected.map((link) => link.call.answered_at).filter((v): v is string => Boolean(v)).sort()[0] ?? null;
  const status = connected.length && answeredAt
    ? formatDuration(elapsedSeconds({ answered_at: answeredAt }, now))
    : t(`appCalls.phase.${first ? linkPhase(first) : 'connecting'}` as MessageKey);
  const name = group ? t('appCalls.group.title') : session.title?.display_name ?? first?.call.peer_name ?? '';
  const camera = session.local.getVideoTracks()[0] ?? null;
  const localVideo = Boolean(session.screen) || Boolean(camera && session.camera);
  const video = group || localVideo || links.some(peerVideo) || session.media === 'video';
  const canControl = links.some((link) => link.engine);
  const inProgress = connected.length > 0;
  const slots = freeSlots(session.room, links.length);
  const exclude = new Set([...(session.room?.members ?? []).filter((m) => m.state === 'joined' || m.state === 'invited').map((m) => m.user_id), ...links.map((link) => link.call.peer_user_id)]);
  // While the people picker is open it owns Escape; the call screen stays as it is.
  const panel = useModal(() => { if (!picking) setCompact(true); });
  const localStream = useMemo(() => session.screen ? new MediaStream([session.screen]) : session.local, [session.screen, session.local]);

  const audio = links.map((link) => <RemoteAudio key={`audio-${link.call.id}`} stream={link.remote} speaker={speaker} />);

  if (compact) {
    return <>
      {audio}
      <div className="appCallPill" role="region" aria-label={t('appCalls.screen', { name })}>
        <span className="appCallPillText"><bdi>{name}</bdi> · <span aria-live="polite">{status}</span></span>
        <button type="button" className="iconButton small" aria-label={t('appCalls.screen', { name })} title={t('appCalls.screen', { name })} onClick={() => setCompact(false)}><Maximize2 aria-hidden="true" /></button>
        <button type="button" className="appCallButton hangup small" onClick={onHangUp}><PhoneOff aria-hidden="true" /><span>{t('appCalls.hangUp')}</span></button>
      </div>
    </>;
  }
  if (picking) {
    return <>
      {audio}
      <div className="appCallLayer">
        <CallPeoplePicker mode="add" exclude={exclude} slots={slots} onClose={() => setPicking(false)}
          onPick={(chosen) => { setPicking(false); for (const person of chosen) onInvite(person); }} />
      </div>
    </>;
  }
  const fullId = `${titleId}-full`;
  const controls = <div className="appCallControls">
    <button type="button" className="appCallButton" aria-pressed={session.muted} onClick={() => onMute(!session.muted)} disabled={!canControl}>{session.muted ? <MicOff aria-hidden="true" /> : <Mic aria-hidden="true" />}<span>{session.muted ? t('appCalls.unmute') : t('appCalls.mute')}</span></button>
    <button type="button" className="appCallButton" aria-pressed={speaker} onClick={() => setSpeaker(!speaker)} disabled={!canControl}>{speaker ? <Volume2 aria-hidden="true" /> : <VolumeX aria-hidden="true" />}<span>{speaker ? t('appCalls.speakerOff') : t('appCalls.speakerOn')}</span></button>
    {camera
      ? <button type="button" className="appCallButton" aria-pressed={!session.camera} onClick={() => onCamera(!session.camera)} disabled={!canControl}>{session.camera ? <CameraOff aria-hidden="true" /> : <Camera aria-hidden="true" />}<span>{session.camera ? t('appCalls.cameraOff') : t('appCalls.cameraOn')}</span></button>
      : videoEnabled && <button type="button" className="appCallButton" onClick={onVideo} disabled={!inProgress}><Video aria-hidden="true" /><span>{t('appCalls.video.turnOn')}</span></button>}
    {videoEnabled && canShareScreen() && (session.screen
      ? <button type="button" className="appCallButton" aria-pressed="true" onClick={onStopShare}><MonitorX aria-hidden="true" /><span>{t('appCalls.screen.stop')}</span></button>
      : <button type="button" className="appCallButton" onClick={onShare} disabled={!inProgress}><MonitorUp aria-hidden="true" /><span>{t('appCalls.screen.share')}</span></button>)}
    <button type="button" className="appCallButton" onClick={() => setPicking(true)} disabled={!inProgress || slots <= 0} aria-describedby={slots <= 0 ? fullId : undefined}><UserPlus aria-hidden="true" /><span>{t('appCalls.group.add')}</span></button>
    <button type="button" className="appCallButton hangup" onClick={onHangUp}><PhoneOff aria-hidden="true" /><span>{t('appCalls.hangUp')}</span></button>
    {slots <= 0 && <span id={fullId} className="visuallyHidden">{t('appCalls.group.full', { count: session.room?.max_people ?? 4 })}</span>}
  </div>;
  return <>
    {audio}
    <div className="appCallLayer">
      <section className={`surface appCallPanel ${video ? 'withVideo' : ''}`} role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel as never}>
        <button type="button" className="iconButton small appCallMinimize" aria-label={t('appCalls.minimise')} title={t('appCalls.minimise')} onClick={() => setCompact(true)}><Minimize2 aria-hidden="true" /></button>
        {group
          ? <ul className="appCallTiles" aria-label={t('appCalls.group.people')}>
              <li className="appCallTile">
                {localVideo ? <VideoOf stream={localStream} label={session.screen ? t('appCalls.screen.yours') : t('appCalls.localVideo')} className="appCallTileVideo" mirrored={!session.screen} /> : <Avatar name={t('appCalls.group.you')} />}
                <span className="appCallTileName">{t('appCalls.group.you')}{session.muted ? ` · ${t('appCalls.group.muted')}` : ''}</span>
              </li>
              {links.map((link) => <li key={link.call.id} className="appCallTile">
                {peerVideo(link) ? <VideoOf stream={link.remote} label={t('appCalls.remoteVideo', { name: link.call.peer_name })} className="appCallTileVideo" /> : <Avatar name={link.call.peer_name} />}
                <span className="appCallTileName"><bdi>{link.call.peer_name}</bdi> · {link.peer?.screen ? t('appCalls.screen.theirs') : t(`appCalls.phase.${linkPhase(link)}` as MessageKey)}{link.peer?.muted ? ` · ${t('appCalls.group.muted')}` : ''}</span>
              </li>)}
            </ul>
          : video
            ? <div className="appCallVideos">
                <VideoOf stream={first?.remote ?? null} label={t('appCalls.remoteVideo', { name })} className="appCallRemote" />
                {first && !peerVideo(first) && <span className="appCallVideoOff"><Avatar name={name} /></span>}
                {localVideo && <VideoOf stream={localStream} label={session.screen ? t('appCalls.screen.yours') : t('appCalls.localVideo')} className="appCallLocal" mirrored={!session.screen} />}
              </div>
            : <Avatar name={name} />}
        <h2 id={titleId}><bdi>{name}</bdi></h2>
        <p className="appCallStatus" aria-live="polite">
          {group ? t('appCalls.group.count', { count: links.length + 1 }) : t(video ? 'appCalls.videoCall' : 'appCalls.voiceCall')} · <span data-testid="call-status">{status}</span>
          {!group && first?.peer?.screen ? <> · {t('appCalls.screen.theirs')}</> : null}
        </p>
        {controls}
      </section>
    </div>
  </>;
}

function EndedScreen({ ended, onClose, onCallAgain }: { ended: NonNullable<Ended>; onClose(): void; onCallAgain(person: Person, media: AppCallMedia): void }) {
  const panel = useModal(onClose);
  const titleId = useId();
  if (ended.group) {
    return <div className="appCallLayer">
      <section className="surface appCallPanel" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel as never}>
        <span className="appCallAvatar" aria-hidden="true"><Users /></span>
        <h2 id={titleId}>{t('appCalls.group.title')}</h2>
        <p className="appCallStatus" role="status">{t('appCalls.word.completed')}</p>
        {ended.group.length > 0 && <p className="appCallDetail">{t('appCalls.group.with', { names: ended.group.join(', ') })}</p>}
        <div className="appCallControls">
          <button type="button" className="appCallButton" onClick={onClose} data-autofocus><X aria-hidden="true" /><span>{t('appCalls.close')}</span></button>
        </div>
      </section>
    </div>;
  }
  const call = ended.call, person = ended.person;
  const word = call ? outcomeWord(call) : null;
  return <div className="appCallLayer">
    <section className="surface appCallPanel" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel as never}>
      <Avatar name={person?.display_name ?? ''} />
      <h2 id={titleId}><bdi>{person?.display_name}</bdi></h2>
      <p className="appCallStatus" role="status">{word ? t(`appCalls.word.${word}` as MessageKey) : t('appCalls.word.completed')}</p>
      {ended.message && <p className="appCallDetail">{ended.message}</p>}
      <div className="appCallControls">
        {person && call && <button type="button" className="appCallButton accept" onClick={() => onCallAgain(person, call.media)}><Phone aria-hidden="true" /><span>{t('appCalls.callAgain')}</span></button>}
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
