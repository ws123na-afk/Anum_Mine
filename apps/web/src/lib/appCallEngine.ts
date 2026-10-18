// One WebRTC peer connection for an in-app call (docs/in-app-calls.md#media). The caller sends
// the offer once the callee has answered; ICE candidates are batched (one request every 150 ms)
// and the callee's candidates wait until the offer is applied. Media never passes the API.
import type { AppCallIceCandidate, AppCallIceServers, AppCallMedia, AppCallSignal } from '@anum/contracts';

export interface EngineApi {
  sendDescription(kind: 'offer' | 'answer', sdp: string): Promise<unknown>;
  sendCandidates(candidates: AppCallIceCandidate[]): Promise<unknown>;
  heartbeat(): Promise<unknown>;
}

export interface EngineEvents {
  onRemoteStream(stream: MediaStream): void;
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

  constructor(
    private readonly role: 'caller' | 'callee',
    readonly local: MediaStream,
    servers: AppCallIceServers,
    private readonly api: EngineApi,
    private readonly events: EngineEvents,
  ) {
    this.pc = new RTCPeerConnection(iceConfig(servers));
    for (const track of local.getTracks()) this.pc.addTrack(track, local);
    this.pc.onicecandidate = (event) => {
      if (!event.candidate || !event.candidate.candidate) return;
      this.pendingOut.push({ candidate: event.candidate.candidate, sdpMid: event.candidate.sdpMid, sdpMLineIndex: event.candidate.sdpMLineIndex });
      if (!this.flushTimer) this.flushTimer = setTimeout(() => this.flush(), ICE_BATCH_MS);
    };
    this.pc.ontrack = (event) => {
      const [stream] = event.streams;
      this.events.onRemoteStream(stream ?? new MediaStream([event.track]));
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
  }

  setCamera(on: boolean): void {
    for (const track of this.local.getVideoTracks()) track.enabled = on;
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    for (const timer of [this.flushTimer, this.graceTimer]) if (timer) clearTimeout(timer);
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    for (const track of this.local.getTracks()) track.stop();
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
