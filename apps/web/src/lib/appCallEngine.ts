// One WebRTC peer connection for an in-app call (docs/in-app-calls.md#media). The caller sends
// the offer once the callee has answered; ICE candidates are batched (one request every 150 ms)
// and the callee's candidates wait until the offer is applied. Media never passes the API.
//
// Every connection negotiates one audio and one video section, even for a voice call, plus a
// small data channel. Turning video on mid-call or sharing a screen then only swaps the track
// on the video sender (no new offer), and each side tells the other over the data channel
// whether its camera or screen is on (docs/in-app-calls.md#video-mid-call). A group call holds
// one engine per other person, all sharing the same local microphone and camera stream.
import type { AppCallIceCandidate, AppCallIceServers, AppCallMedia, AppCallSignal } from '@anum/contracts';
import { parsePeerState, type PeerMediaState } from './appCalls';

export type { PeerMediaState } from './appCalls';

export interface EngineApi {
  sendDescription(kind: 'offer' | 'answer', sdp: string): Promise<unknown>;
  sendCandidates(candidates: AppCallIceCandidate[]): Promise<unknown>;
  heartbeat(): Promise<unknown>;
}

export interface EngineEvents {
  onRemoteStream(stream: MediaStream): void;
  /** The other side's camera, screen and microphone state (from its data channel). */
  onPeerState?(state: PeerMediaState): void;
  /** connected: media flows; reconnecting: ICE lost the path for now; failed: it gave up. */
  onConnection(state: 'connecting' | 'connected' | 'reconnecting' | 'failed'): void;
}

export const HEARTBEAT_MS = 15_000;
const ICE_BATCH_MS = 150;
const RECONNECT_GRACE_MS = 10_000;

/** Asks for the microphone (and the camera for a video call) before ringing or answering. */
export async function openLocalMedia(media: AppCallMedia): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) throw Object.assign(new Error('getUserMedia is unavailable'), { name: 'SecurityError' });
  return navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    video: media === 'video' ? { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' } : false,
  });
}

export function iceConfig(servers: AppCallIceServers): RTCConfiguration {
  return {
    iceServers: servers.ice_servers.map((server) => ({
      urls: server.urls,
      ...(server.username ? { username: server.username } : {}),
      ...(server.credential ? { credential: server.credential } : {}),
    })),
    bundlePolicy: 'max-bundle',
  };
}

export class AppCallEngine {
  readonly pc: RTCPeerConnection;
  private pendingOut: AppCallIceCandidate[] = [];
  private pendingIn: RTCIceCandidateInit[] = [];
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private graceTimer: ReturnType<typeof setTimeout> | null = null;
  private remoteSet = false;
  private closed = false;
  private readonly channel: RTCDataChannel;
  private readonly remote = new MediaStream();
  private state: PeerMediaState = { camera: false, screen: false, muted: false };

  constructor(
    private readonly role: 'caller' | 'callee',
    readonly local: MediaStream,
    servers: AppCallIceServers,
    private readonly api: EngineApi,
    private readonly events: EngineEvents,
  ) {
    this.pc = new RTCPeerConnection(iceConfig(servers));
    for (const track of local.getTracks()) this.pc.addTrack(track, local);
    // A video section even without a camera, so video can be turned on later without a new offer.
    if (role === 'caller' && !local.getVideoTracks().length) this.pc.addTransceiver('video', { direction: 'sendrecv', streams: [local] });
    this.state.camera = local.getVideoTracks().some((track) => track.enabled);
    this.channel = this.pc.createDataChannel('anum-state', { negotiated: true, id: 0 });
    this.channel.onopen = () => this.pushState();
    this.channel.onmessage = (event) => { const state = parsePeerState(event.data); if (state) this.events.onPeerState?.(state); };
    this.pc.onicecandidate = (event) => {
      if (!event.candidate || !event.candidate.candidate) return;
      this.pendingOut.push({ candidate: event.candidate.candidate, sdpMid: event.candidate.sdpMid, sdpMLineIndex: event.candidate.sdpMLineIndex });
      if (!this.flushTimer) this.flushTimer = setTimeout(() => this.flush(), ICE_BATCH_MS);
    };
    this.pc.ontrack = (event) => {
      for (const track of this.remote.getTracks()) if (track.kind === event.track.kind && track !== event.track) this.remote.removeTrack(track);
      if (!this.remote.getTracks().includes(event.track)) this.remote.addTrack(event.track);
      // A new MediaStream object each time, so React sees the change.
      this.events.onRemoteStream(new MediaStream(this.remote.getTracks()));
    };
    this.pc.onconnectionstatechange = () => this.connectionChanged();
    this.events.onConnection('connecting');
  }

  /** The caller, once the call is answered. */
  async sendOffer(): Promise<void> {
    if (this.role !== 'caller') return;
    const offer = await this.pc.createOffer();
    await this.pc.setLocalDescription(offer);
    await this.api.sendDescription('offer', offer.sdp ?? '');
  }

  async handleSignal(signal: AppCallSignal): Promise<void> {
    if (this.closed) return;
    if (signal.kind === 'offer' && this.role === 'callee' && signal.payload.sdp) {
      await this.pc.setRemoteDescription({ type: 'offer', sdp: signal.payload.sdp });
      // Answer the video section as send-and-receive even when answering by voice only.
      for (const transceiver of this.pc.getTransceivers()) {
        if (transceiver.receiver.track.kind === 'video' && transceiver.direction === 'recvonly') transceiver.direction = 'sendrecv';
      }
      await this.remoteReady();
      const answer = await this.pc.createAnswer();
      await this.pc.setLocalDescription(answer);
      await this.api.sendDescription('answer', answer.sdp ?? '');
    } else if (signal.kind === 'answer' && this.role === 'caller' && signal.payload.sdp) {
      await this.pc.setRemoteDescription({ type: 'answer', sdp: signal.payload.sdp });
      await this.remoteReady();
    } else if (signal.kind === 'ice') {
      for (const candidate of signal.payload.candidates ?? []) {
        const init: RTCIceCandidateInit = { candidate: candidate.candidate, sdpMid: candidate.sdpMid, sdpMLineIndex: candidate.sdpMLineIndex };
        if (this.remoteSet) await this.pc.addIceCandidate(init).catch(() => undefined);
        else this.pendingIn.push(init);
      }
    }
  }

  setMuted(muted: boolean): void {
    for (const track of this.local.getAudioTracks()) track.enabled = !muted;
    this.state = { ...this.state, muted };
    this.pushState();
  }

  setCamera(on: boolean): void {
    for (const track of this.local.getVideoTracks()) track.enabled = on;
    this.state = { ...this.state, camera: on };
    this.pushState();
  }

  /** Whether this connection has a negotiated video section (an older app's offer may not). */
  canSendVideo(): boolean {
    return Boolean(this.videoTransceiver()?.mid);
  }

  /** Sends ``track`` (the camera, a shared screen, or nothing) on the video section. */
  async setVideoTrack(track: MediaStreamTrack | null, state: { camera: boolean; screen: boolean }): Promise<void> {
    const transceiver = this.videoTransceiver();
    if (!transceiver) throw new Error('This connection has no video');
    await transceiver.sender.replaceTrack(track);
    this.state = { ...this.state, ...state };
    this.pushState();
  }

  private videoTransceiver(): RTCRtpTransceiver | undefined {
    return this.pc.getTransceivers().find((t) => t.receiver.track.kind === 'video' && t.direction !== 'stopped');
  }

  private pushState(): void {
    if (this.channel.readyState === 'open') {
      try { this.channel.send(JSON.stringify(this.state)); } catch { /* the next change resends */ }
    }
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    for (const timer of [this.flushTimer, this.graceTimer]) if (timer) clearTimeout(timer);
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    // The local stream belongs to the call (shared by a group call's connections); the caller stops it.
    this.channel.onopen = this.channel.onmessage = null;
    this.pc.onicecandidate = this.pc.ontrack = this.pc.onconnectionstatechange = null;
    this.pc.close();
  }

  private async remoteReady(): Promise<void> {
    this.remoteSet = true;
    const queued = this.pendingIn.splice(0);
    for (const init of queued) await this.pc.addIceCandidate(init).catch(() => undefined);
  }

  private flush(): void {
    this.flushTimer = null;
    const batch = this.pendingOut.splice(0, 20);
    if (batch.length) void this.api.sendCandidates(batch).catch(() => undefined);
    if (this.pendingOut.length) this.flushTimer = setTimeout(() => this.flush(), ICE_BATCH_MS);
  }

  private connectionChanged(): void {
    const state = this.pc.connectionState;
    if (state === 'connected') {
      if (this.graceTimer) { clearTimeout(this.graceTimer); this.graceTimer = null; }
      if (!this.heartbeatTimer) {
        void this.api.heartbeat().catch(() => undefined);
        this.heartbeatTimer = setInterval(() => void this.api.heartbeat().catch(() => undefined), HEARTBEAT_MS);
      }
      this.events.onConnection('connected');
    } else if (state === 'disconnected') {
      this.events.onConnection('reconnecting');
      if (!this.graceTimer) this.graceTimer = setTimeout(() => { if (this.pc.connectionState !== 'connected') this.events.onConnection('failed'); }, RECONNECT_GRACE_MS);
    } else if (state === 'failed') {
      this.events.onConnection('failed');
    }
  }
}
