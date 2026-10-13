import { expect, type Page } from '@playwright/test';
import { mockWorkspaceApi } from './workspaceApi';

// Voice call mode (docs/voice.md#call-mode) with mocked audio APIs: the microphone is an oscillator
// whose loudness the test controls (so voice activity detection really runs), recognition is a fake
// that hears queued phrases, speech synthesis "speaks" for a set time, and the camera is a canvas.
const now = '2026-10-11T09:00:00.000Z';
const workspace = { tasks_total: 2, running: 1, waiting_approval: 0, pending_approvals: 1 };

export interface CallMock { asked: string[]; looked: number; summaries: { save: boolean }[]; completed: number; frames: string[] }

export async function mockCallApi(page: Page, options: { vision?: boolean; failAsk?: boolean } = {}): Promise<CallMock> {
  await mockWorkspaceApi(page);
  const mock: CallMock = { asked: [], looked: 0, summaries: [], completed: 0, frames: [] };
  const texts = new Map<string, string>();
  let sequence = 0;
  await page.route('http://localhost:8000/api/v1/voice/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
    if (path === '/api/v1/voice/capabilities') {
      return json({
        stt: [{ id: 'device', kind: 'device', available: true, label: 'Device', model: null, usd_per_minute: null, usd_per_million_chars: null, reason: null },
          { id: 'server', kind: 'server', available: false, label: 'Not configured', model: null, usd_per_minute: null, usd_per_million_chars: null, reason: 'Server recognition is off.' }],
        tts: [{ id: 'device', kind: 'device', available: true, label: 'Device', model: null, usd_per_minute: null, usd_per_million_chars: null, reason: null }],
        vision: options.vision ? { available: true, model: 'claude-haiku-5-5', reason: null, max_frame_bytes: 1500000, media_types: ['image/jpeg'] }
          : { available: false, model: null, reason: 'Camera answers need a vision model. An owner can choose Anthropic in Model settings.', max_frame_bytes: 1500000, media_types: ['image/jpeg'] },
        latency_target_ms: { first_audio: 1500, barge_in: 300, end_of_turn: 700 },
        locales: ['en-US', 'en-GB', 'ar-SA', 'ar-AE'], max_audio_bytes: 4194304, max_audio_ms: 120000, max_speech_chars: 600,
      });
    }
    if (path === '/api/v1/voice/sessions' && request.method() === 'GET') return route.fallback();
    if (path === '/api/v1/voice/sessions') return json({ id: 'voice_call', locale: 'en-US', retention: 'session', assistant_name: 'Anum', status: 'active', created_at: now, updated_at: now, expires_at: null }, 201);
    if (path.endsWith('/transcript') && request.method() === 'POST') {
      const body = request.postDataJSON() as { text: string };
      const id = `seg_${sequence++}`;
      texts.set(id, body.text);
      mock.asked.push(body.text);
      return json({ id, session_id: 'voice_call', role: 'user', text: body.text, is_final: true, client_sequence: sequence, created_at: now }, 201);
    }
    if (path.endsWith('/ask/stream') || path.endsWith('/look/stream')) {
      const body = request.postDataJSON() as { transcript_segment_id: string; image_base64?: string };
      const text = texts.get(body.transcript_segment_id) ?? '';
      if (options.failAsk) return route.fulfill({ contentType: 'text/event-stream', body: frame('meta', { intent: 'question', risk_tier: 'read', proposed_task: null, workspace }) + frame('error', { code: 'model_unavailable', message: 'I could not answer. Try again in a moment.' }) });
      if (body.image_base64) { mock.looked += 1; mock.frames.push(body.image_base64.slice(0, 8)); }
      const reply = body.image_base64 ? { intent: 'question', risk_tier: 'read', proposed_task: null, text: 'I can see a desk with a blue mug.' }
        : text.startsWith('Create a task') ? { intent: 'create_task', risk_tier: 'confirm', proposed_task: 'call the tyre supplier', text: 'Got it. Tap Create task and I will set it up.' }
        : text.startsWith('Approve') ? { intent: 'visual_only', risk_tier: 'visual_only', proposed_task: null, text: 'That one needs your own tap on screen, so I will not do it by voice.' }
        : { intent: 'status', risk_tier: 'read', proposed_task: null, text: 'You have two tasks and one is running right now. One approval is waiting for you as well.' };
      const words = reply.text.split(/(?<= )/);
      return route.fulfill({
        contentType: 'text/event-stream',
        body: frame('meta', { intent: reply.intent, risk_tier: reply.risk_tier, proposed_task: reply.proposed_task, workspace, vision: Boolean(body.image_base64) })
          + words.map((word) => frame('delta', { text: word })).join('')
          + frame('done', { assistant_segment: { id: `a_${body.transcript_segment_id}`, session_id: 'voice_call', role: 'assistant', text: reply.text, is_final: true, client_sequence: 0, created_at: now } }),
      });
    }
    if (path.endsWith('/commands')) return json({ session: {}, transcript_segment_id: 'x', task: { id: 'task_call', title: 'call the tyre supplier', prompt: 'call the tyre supplier', status: 'created', tenant_id: 't', workspace_id: 'w', created_at: now, updated_at: now } });
    if (path.endsWith('/summary')) {
      const body = request.postDataJSON() as { save: boolean; duration_seconds: number };
      mock.summaries.push({ save: body.save });
      return json({ summary: '- Asked about the workspace status.', source: 'model', conversation_id: 'conv_1', turns_saved: 3, duration_seconds: body.duration_seconds });
    }
    if (path.endsWith('/complete')) { mock.completed += 1; return json({ id: 'voice_call', status: 'completed' }); }
    return route.fallback();
  });
  return mock;
}

export async function fakeMedia(page: Page, options: { denyMic?: boolean; noRecognition?: boolean } = {}) {
  await page.addInitScript((opts) => {
    const state = { gain: null as GainNode | null, speakMs: 2500, cancels: 0, spoken: [] as string[], queue: [] as string[] };
    Object.assign(window, { __call: state });
    (window as unknown as { __loud: (on: boolean) => void }).__loud = (on) => { if (state.gain) state.gain.gain.value = on ? 0.8 : 0.0005; };
    (window as unknown as { __say: (text: string) => void }).__say = (text) => { state.queue.push(text); };
    const media = navigator.mediaDevices ?? ({} as MediaDevices);
    media.getUserMedia = async (constraints?: MediaStreamConstraints) => {
      if (constraints?.video) {
        const canvas = document.createElement('canvas');
        canvas.width = 320; canvas.height = 180;
        const context = canvas.getContext('2d')!;
        const draw = () => { context.fillStyle = '#2563eb'; context.fillRect(0, 0, 320, 180); context.fillStyle = '#fff'; context.fillRect(40 + (Date.now() / 20) % 200, 60, 40, 40); requestAnimationFrame(draw); };
        draw();
        return canvas.captureStream(15);
      }
      if (opts.denyMic) throw new DOMException('Permission denied', 'NotAllowedError');
      const audio = new AudioContext();
      void audio.resume();
      const oscillator = audio.createOscillator();
      oscillator.frequency.value = 220;
      const gain = audio.createGain();
      gain.gain.value = 0.0005;
      const destination = audio.createMediaStreamDestination();
      oscillator.connect(gain).connect(destination);
      oscillator.start();
      state.gain = gain;
      return destination.stream;
    };
    Object.defineProperty(navigator, 'mediaDevices', { value: media, configurable: true });
    if (!opts.noRecognition) {
      class FakeRecognition {
        continuous = false; interimResults = false; lang = 'en-US'; results: { 0: { transcript: string }; isFinal: boolean }[] = [];
        onresult: ((event: unknown) => void) | null = null; onerror: ((event: unknown) => void) | null = null; onend: (() => void) | null = null;
        private timer = 0;
        start() {
          const poll = () => {
            const phrase = state.queue.shift();
            if (phrase !== undefined) {
              this.results.push(Object.assign([{ transcript: phrase }], { isFinal: true }) as unknown as { 0: { transcript: string }; isFinal: boolean });
              this.onresult?.({ results: this.results });
            }
            this.timer = window.setTimeout(poll, 50);
          };
          poll();
        }
        stop() { window.clearTimeout(this.timer); }
        abort() { window.clearTimeout(this.timer); }
      }
      Object.assign(window, { SpeechRecognition: FakeRecognition, webkitSpeechRecognition: FakeRecognition });
    } else {
      Object.assign(window, { SpeechRecognition: undefined, webkitSpeechRecognition: undefined });
    }
    let current: SpeechSynthesisUtterance | null = null;
    let timer = 0;
    const synthesis = {
      speak(utterance: SpeechSynthesisUtterance) {
        current = utterance;
        state.spoken.push(utterance.text);
        window.setTimeout(() => utterance.onstart?.(new Event('start') as SpeechSynthesisEvent), 10);
        timer = window.setTimeout(() => { if (current === utterance) { current = null; utterance.onend?.(new Event('end') as SpeechSynthesisEvent); } }, state.speakMs);
      },
      cancel() {
        state.cancels += 1;
        window.clearTimeout(timer);
        const utterance = current;
        current = null;
        utterance?.onend?.(new Event('end') as SpeechSynthesisEvent);
      },
      getVoices: () => [], addEventListener: () => undefined, removeEventListener: () => undefined, speaking: false, pending: false, paused: false,
    };
    Object.defineProperty(window, 'speechSynthesis', { value: synthesis, configurable: true });
    localStorage.setItem('anum.call.tts', 'device');
  }, options);
}

export async function startCall(page: Page) {
  await page.goto('/#call');
  await page.getByRole('button', { name: 'Start call' }).click();
  await expect(page.getByRole('region', { name: 'Call with Anum' })).toBeVisible();
}

