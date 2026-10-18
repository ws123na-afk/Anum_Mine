// In-app calls between teammates (docs/in-app-calls.md, anum_api/app_calls.py).

export type AppCallMedia = 'audio' | 'video';
export type AppCallStatus = 'ringing' | 'accepted' | 'ended';
export type AppCallOutcome = 'completed' | 'declined' | 'missed' | 'cancelled' | 'busy' | 'failed' | 'dropped';
export type AppCallMode = 'off' | 'stun_only' | 'turn';

/** One call as the signed-in participant sees it. */
export interface AppCall {
  id: string;
  direction: 'outgoing' | 'incoming';
  peer_user_id: string;
  peer_name: string;
  media: AppCallMedia;
  status: AppCallStatus;
  outcome: AppCallOutcome | null;
  created_at: string;
  answered_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  ring_expires_at: string;
  /** Stream only: whether this device is the one bound to the call. */
  on_this_device?: boolean | null;
  answered_elsewhere: boolean;
}

export interface AppCallPage { items: AppCall[]; next_cursor: string | null }

export interface AppCallConfig {
  enabled: boolean;
  mode: AppCallMode;
  reason: string | null;
  video_enabled: boolean;
  ring_timeout_seconds: number;
}

export interface AppCallIceServer { urls: string[]; username: string | null; credential: string | null }
export interface AppCallIceServers { mode: AppCallMode; ice_servers: AppCallIceServer[]; expires_at: string | null; ttl_seconds: number | null }

export interface AppCallIceCandidate { candidate: string; sdpMid: string | null; sdpMLineIndex: number | null }
export type AppCallSignalKind = 'offer' | 'answer' | 'ice';
/** A signal addressed to this device, as the call stream sends it. */
export interface AppCallSignal {
  id: string;
  call_id: string;
  kind: AppCallSignalKind;
  payload: { type?: 'offer' | 'answer'; sdp?: string; candidates?: AppCallIceCandidate[] };
}
export type AppCallEndReason = 'hangup' | 'decline' | 'failed' | 'busy';
