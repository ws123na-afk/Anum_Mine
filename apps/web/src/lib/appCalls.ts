// In-app calls between teammates (docs/in-app-calls.md): the pure parts, with no browser API, so
// `node --test` runs them: device ids, the call screen's phase, outcome wording, durations, the
// SSE frame parser and the microphone/camera error kinds.
import type { AppCall, AppCallOutcome } from '@anum/contracts';

/** A random device id for this tab (base64url, 22 characters); the API binds a call to it. */
export function newClientId(random: (bytes: Uint8Array<ArrayBuffer>) => Uint8Array<ArrayBuffer> = (b) => crypto.getRandomValues(b)): string {
  const bytes = random(new Uint8Array(new ArrayBuffer(16)));
  let text = '';
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** 0:07, 12:30, 1:02:03. */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** Seconds since the call was answered, for the live timer. */
export function elapsedSeconds(call: Pick<AppCall, 'answered_at'>, now: number = Date.now()): number {
  return call.answered_at ? Math.max(0, Math.floor((now - Date.parse(call.answered_at)) / 1000)) : 0;
}

export type OutcomeWord =
  | 'completed' | 'missed' | 'noAnswer' | 'cancelled' | 'declinedByYou' | 'declinedByPeer' | 'busyYou' | 'busyPeer' | 'failed' | 'dropped';

/** How one participant reads an outcome (a cancelled ring is a missed call for the callee). */
export function outcomeWord(call: Pick<AppCall, 'direction' | 'outcome'>): OutcomeWord | null {
  const incoming = call.direction === 'incoming';
  const outcome: AppCallOutcome | null = call.outcome;
  switch (outcome) {
    case null: return null;
    case 'completed': return 'completed';
    case 'missed': return incoming ? 'missed' : 'noAnswer';
    case 'cancelled': return incoming ? 'missed' : 'cancelled';
    case 'busy': return incoming ? 'missed' : 'busyPeer';
    case 'declined': return incoming ? 'declinedByYou' : 'declinedByPeer';
    case 'failed': return 'failed';
    case 'dropped': return 'dropped';
  }
}

export function isMissed(call: Pick<AppCall, 'direction' | 'outcome'>): boolean {
  return outcomeWord(call) === 'missed';
}

/**
 * What the call screen shows. "calling" until the callee's device is reachable is not knowable,
 * so an outgoing ring is "ringing" from the start and becomes "connecting" when answered.
 */
export type CallPhase = 'idle' | 'preparing' | 'ringing' | 'incoming' | 'connecting' | 'connected' | 'reconnecting' | 'ended';

export function phaseFor(call: AppCall | null, media: { connected: boolean; reconnecting: boolean }): CallPhase {
  if (!call) return 'idle';
  if (call.status === 'ended') return 'ended';
  if (call.status === 'ringing') return call.direction === 'incoming' ? 'incoming' : 'ringing';
  if (media.reconnecting) return 'reconnecting';
  return media.connected ? 'connected' : 'connecting';
}

export type MediaProblem = 'denied' | 'noDevice' | 'inUse' | 'insecure' | 'other';

/** getUserMedia's DOMException names, as the call screen explains them. */
export function mediaProblem(error: unknown): MediaProblem {
  const name = typeof error === 'object' && error && 'name' in error ? String((error as { name: unknown }).name) : '';
  if (name === 'NotAllowedError' || name === 'PermissionDeniedError') return 'denied';
  if (name === 'NotFoundError' || name === 'OverconstrainedError' || name === 'DevicesNotFoundError') return 'noDevice';
  if (name === 'NotReadableError' || name === 'TrackStartError' || name === 'AbortError') return 'inUse';
  if (name === 'SecurityError' || name === 'TypeError') return 'insecure';
  return 'other';
}

export interface SseFrame { event: string; id: string | null; data: string }

/** Splits a server-sent event buffer into complete frames and the unfinished rest. */
export function parseSse(buffer: string): { frames: SseFrame[]; rest: string } {
  const parts = buffer.replace(/\r\n/g, '\n').split('\n\n');
  const rest = parts.pop() ?? '';
  const frames: SseFrame[] = [];
  for (const part of parts) {
    let event = 'message', id: string | null = null;
    const data: string[] = [];
    for (const line of part.split('\n')) {
      if (!line || line.startsWith(':')) continue;
      const colon = line.indexOf(':');
      const field = colon < 0 ? line : line.slice(0, colon);
      const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '');
      if (field === 'event') event = value;
      else if (field === 'id') id = value;
      else if (field === 'data') data.push(value);
    }
    if (data.length) frames.push({ event, id, data: data.join('\n') });
  }
  return { frames, rest };
}

/** Backoff for the call stream's reconnects: 0.5 s after a clean end, then 1, 2, 4 ... 15 s. */
export function reconnectDelay(failures: number): number {
  return failures <= 0 ? 500 : Math.min(15_000, 1000 * 2 ** (failures - 1));
}

/** Initials for the call screen's avatar ("Sara Ali" → "SA"). */
export function initialsOf(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '?';
  const letters = words.length === 1 ? [...words[0]].slice(0, 2) : [[...words[0]][0], [...words[words.length - 1]][0]];
  return letters.join('').toUpperCase();
}
