import type { Page, Route } from '@playwright/test';

// A stand-in for the API's call signaling (anum_api/app_calls.py) shared by two browser
// contexts: each page is one teammate, so a call placed in one page rings in the other and the
// real WebRTC offer, answer and ICE candidates travel between them. The call stream answers as a
// long poll (the web client reconnects after each response, as it does after any clean end).

interface StoredCall {
  id: string; caller: string; callee: string; callerName: string; calleeName: string; callerClient: string; calleeClient: string | null;
  media: 'audio' | 'video'; status: 'ringing' | 'accepted' | 'ended'; outcome: string | null;
  created_at: string; answered_at: string | null; ended_at: string | null; duration_seconds: number | null;
}
interface StoredSignal { id: string; call_id: string; to: string; toClient: string; kind: string; payload: Record<string, unknown> }

export interface Teammate { user_id: string; name: string }

export class CallRelay {
  readonly calls: StoredCall[] = [];
  readonly signals: StoredSignal[] = [];
  readonly ended: string[] = [];
  private sequence = 0;
  private wake = new Set<() => void>();

  constructor(readonly options: { mode?: 'turn' | 'stun_only' | 'off' } = {}) {}

  private notify() { for (const resolve of [...this.wake]) resolve(); }

  view(call: StoredCall, me: string, client?: string) {
    const outgoing = call.caller === me;
    return {
      id: call.id, direction: outgoing ? 'outgoing' : 'incoming', peer_user_id: outgoing ? call.callee : call.caller,
      peer_name: outgoing ? call.calleeName : call.callerName, media: call.media, status: call.status, outcome: call.outcome,
      created_at: call.created_at, answered_at: call.answered_at, ended_at: call.ended_at, duration_seconds: call.duration_seconds,
      ring_expires_at: new Date(Date.parse(call.created_at) + 45_000).toISOString(),
      on_this_device: client ? (outgoing ? call.callerClient : call.calleeClient) === client : null,
      answered_elsewhere: Boolean(client && !outgoing && call.calleeClient && call.calleeClient !== client),
    };
  }

  /** Routes this page's /api/v1/calls traffic as `me`; `people` are the teammates it can ring. */
  async attach(page: Page, me: Teammate, people: Teammate[]): Promise<void> {
    const sent = new Map<string, Map<string, string>>();
    const delivered = new Map<string, Set<string>>();
    await page.route((url) => url.host === 'localhost:8000' && url.pathname.startsWith('/api/v1/calls'), async (route: Route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname;
      const method = request.method();
      const body = request.postData() ? request.postDataJSON() as Record<string, unknown> : {};
      const json = (data: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
      const mode = this.options.mode ?? 'turn';
      if (path === '/api/v1/calls/config') {
        return json({ enabled: mode !== 'off', mode, reason: mode === 'off' ? 'Calls are off on this server until a relay (TURN) server is configured' : null, video_enabled: true, ring_timeout_seconds: 45 });
      }
      if (path === '/api/v1/calls/ice-servers') return json({ mode, ice_servers: [], expires_at: null, ttl_seconds: null });
      if (path === '/api/v1/calls' && method === 'POST') {
        const callee = people.find((p) => p.user_id === body.callee_user_id);
        if (!callee) return json({ error: { code: 'not_found', message: 'This person is not an active member of this workspace', correlation_id: 'c', details: [] } }, 404);
        const call: StoredCall = {
          id: `call_${++this.sequence}`, caller: me.user_id, callee: callee.user_id, callerName: me.name, calleeName: callee.name,
          callerClient: String(body.client_id), calleeClient: null, media: body.media === 'video' ? 'video' : 'audio', status: 'ringing', outcome: null,
          created_at: new Date().toISOString(), answered_at: null, ended_at: null, duration_seconds: null,
        };
        this.calls.push(call);
        this.notify();
        return json(this.view(call, me.user_id, call.callerClient), 201);
      }
      if (path === '/api/v1/calls' && method === 'GET') {
        const mine = this.calls.filter((c) => c.caller === me.user_id || c.callee === me.user_id).reverse().map((c) => this.view(c, me.user_id));
        return json({ items: url.searchParams.get('missed') === 'true' ? mine.filter((c) => c.direction === 'incoming' && ['missed', 'cancelled', 'busy'].includes(String(c.outcome))) : mine, next_cursor: null });
      }
      const match = /^\/api\/v1\/calls\/([^/]+)\/(accept|end|heartbeat|signals)$/.exec(path);
      if (match) {
        const call = this.calls.find((c) => c.id === match[1] && (c.caller === me.user_id || c.callee === me.user_id));
        if (!call) return json({ error: { code: 'not_found', message: 'Call not found', correlation_id: 'c', details: [] } }, 404);
        const action = match[2];
        if (action === 'accept') {
          call.status = 'accepted'; call.answered_at = new Date().toISOString(); call.calleeClient = String(body.client_id);
        } else if (action === 'end') {
          if (call.status !== 'ended') {
            const caller = call.caller === me.user_id;
            call.outcome = body.reason === 'failed' ? 'failed' : call.status === 'accepted' ? 'completed' : caller ? 'cancelled' : body.reason === 'busy' ? 'busy' : 'declined';
            call.ended_at = new Date().toISOString();
            if (call.answered_at) call.duration_seconds = Math.round((Date.parse(call.ended_at) - Date.parse(call.answered_at)) / 1000);
            call.status = 'ended';
            this.ended.push(`${call.id}:${call.outcome}`);
          }
        } else if (action === 'heartbeat') {
          return route.fulfill({ status: 204 });
        } else {
          const caller = call.caller === me.user_id;
          const payload = body.kind === 'ice' ? { candidates: body.candidates } : { type: body.kind, sdp: body.sdp };
          this.signals.push({ id: `sig_${++this.sequence}`, call_id: call.id, to: caller ? call.callee : call.caller, toClient: (caller ? call.calleeClient : call.callerClient) ?? '', kind: String(body.kind), payload });
          this.notify();
          return json({ id: 'sig' }, 202);
        }
        this.notify();
        return json(this.view(call, me.user_id, String(body.client_id ?? '')));
      }
      if (path === '/api/v1/calls/stream') {
        const client = url.searchParams.get('client_id') ?? '';
        const states = sent.get(client) ?? new Map<string, string>();
        sent.set(client, states);
        const seen = delivered.get(client) ?? new Set<string>();
        delivered.set(client, seen);
        const pending = () => {
          const frames: string[] = [];
          for (const call of this.calls) {
            if (call.caller !== me.user_id && call.callee !== me.user_id) continue;
            const key = `${call.status}:${call.outcome}:${call.calleeClient}`;
            if (states.get(call.id) === key) continue;
            states.set(call.id, key);
            frames.push(`id: ${call.id}\nevent: call\ndata: ${JSON.stringify(this.view(call, me.user_id, client))}\n\n`);
          }
          for (const signal of this.signals) {
            if (signal.to !== me.user_id || signal.toClient !== client || seen.has(signal.id)) continue;
            seen.add(signal.id);
            frames.push(`id: ${signal.id}\nevent: signal\ndata: ${JSON.stringify({ id: signal.id, call_id: signal.call_id, kind: signal.kind, payload: signal.payload })}\n\n`);
          }
          return frames;
        };
        let frames = pending();
        if (!frames.length) {
          await new Promise<void>((resolve) => {
            const done = () => { this.wake.delete(done); clearTimeout(timer); resolve(); };
            const timer = setTimeout(done, 700);
            this.wake.add(done);
          });
          frames = pending();
        }
        return route.fulfill({ status: 200, contentType: 'text/event-stream', body: `retry: 1000\n\n${frames.join('')}` }).catch(() => undefined);
      }
      return route.fallback();
    });
  }
}
