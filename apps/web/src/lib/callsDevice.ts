// Calls through the voice provider from the browser (docs/calls.md#browser-calls): Twilio's Voice
// JavaScript SDK, loaded only when a person starts such a call (its own chunk). The access token
// allows outgoing calls through the workspace's TwiML app only, and the call names the call log
// entry ANUM prepared; ANUM's webhook dials the number from that entry, never from the browser.

export type LiveState = 'connecting' | 'ringing' | 'open' | 'ended' | 'failed';

export interface LiveCall {
  /** Stops the call (and the device). Safe to call twice. */
  hangUp(): void;
  setMuted(muted: boolean): void;
}

export interface LiveCallEvents {
  onState(state: LiveState, detail?: string): void;
}

/** Start a provider call for the prepared call log entry `callId`. */
export async function startBrowserCall(token: string, callId: string, events: LiveCallEvents): Promise<LiveCall> {
  const { Device } = await import('@twilio/voice-sdk');
  const device = new Device(token, { closeProtection: true, logLevel: 'error' });
  let ended = false;
  const finish = (state: LiveState, detail?: string) => {
    if (ended) return;
    ended = true;
    events.onState(state, detail);
    try { device.destroy(); } catch { /* already gone */ }
  };
  events.onState('connecting');
  let call: Awaited<ReturnType<typeof device.connect>>;
  try {
    call = await device.connect({ params: { anum_call_id: callId } });
  } catch (error) {
    finish('failed', error instanceof Error ? error.message : String(error));
    throw error;
  }
  call.on('ringing', () => events.onState('ringing'));
  call.on('accept', () => events.onState('open'));
  call.on('disconnect', () => finish('ended'));
  call.on('cancel', () => finish('ended'));
  call.on('reject', () => finish('ended'));
  call.on('error', (error: { message?: string }) => finish('failed', error?.message));
  return {
    hangUp() { try { call.disconnect(); } catch { /* already ended */ } finish('ended'); },
    setMuted(muted: boolean) { call.mute(muted); },
  };
}
