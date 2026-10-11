// The voice call loop (docs/voice.md#call-mode): listen, end the turn on silence, stream the
// reply and speak it sentence by sentence, and stop it the moment the caller talks over it
// (barge-in). Pure: the microphone, recognizer, voice, camera, API and clock come in as
// dependencies, so `pnpm test:unit` drives whole calls with fakes.
import { takeSentences } from './chat.ts';
import { createVad, DEVICE_ONLY, type VadOptions, type VoiceCapabilities } from './call.ts';
import { ApiError, isBudgetExceeded } from './errors.ts';

export type CallPhase = 'idle' | 'connecting' | 'listening' | 'hearing' | 'thinking' | 'speaking' | 'ending' | 'ended';

export type CallProblemKind =
  | 'mic-denied' | 'mic-missing' | 'offline' | 'provider' | 'budget' | 'limit' | 'session'
  | 'typing-only' | 'camera-denied' | 'camera-missing' | 'vision-off' | 'heard-nothing' | 'summary' | 'forbidden';

export interface CallProblem { kind: CallProblemKind; detail?: string }

export interface AskMeta {
  intent: string;
  risk_tier: 'read' | 'confirm' | 'visual_only';
  proposed_task: string | null;
  workspace: { tasks_total: number; running: number; waiting_approval: number; pending_approvals: number };
  vision?: boolean;
}

export interface CallTurn {
  id: string;
  speaker: 'you' | 'assistant';
  text: string;
  meta?: AskMeta;
  resolved?: 'created' | 'dismissed';
  /** A camera frame went with this question (the frame itself is not kept). */
  withFrame?: boolean;
}

export interface Frame { base64: string; mediaType: string }

export interface StreamHandlers { onMeta: (meta: AskMeta) => void; onText: (text: string) => void }

export interface CallSummaryResult { summary: string; source: string; conversation_id: string | null; turns_saved: number; duration_seconds: number }

export interface CallApi {
  createSession(locale: string, retention: string, name: string): Promise<{ id: string }>;
  appendTranscript(sessionId: string, text: string, sequence: number): Promise<{ id: string }>;
  ask(sessionId: string, segmentId: string, signal: AbortSignal, handlers: StreamHandlers): Promise<void>;
  look(sessionId: string, segmentId: string, frame: Frame, signal: AbortSignal, handlers: StreamHandlers): Promise<void>;
  transcribe(sessionId: string, audio: Blob, sequence: number, durationMs: number): Promise<{ id: string; text: string }>;
  createTask(sessionId: string, segmentId: string): Promise<{ title: string }>;
  summarize(sessionId: string, durationSeconds: number, save: boolean): Promise<CallSummaryResult>;
  complete(sessionId: string): Promise<void>;
}

export interface AudioRecorder { start(): void; stop(): Promise<{ blob: Blob; durationMs: number } | null> }

export interface MicHandle {
  /** The current input level (0..1), or a negative number when it cannot be measured. */
  level(): number;
  setEnabled(on: boolean): void;
  close(): void;
  /** A recorder for one turn, for server recognition; null when the browser cannot record. */
  recorder(): AudioRecorder | null;
}

export interface Recognizer {
  start(): void;
  stop(): void;
  /** Forget what was heard so far: the next text reported is the next turn's. */
  mark(): void;
}

export interface Speaker {
  /** Speaks and resolves when done (or stopped). `onStart` fires when sound starts. */
  speak(text: string, onStart: () => void): Promise<void>;
  stop(): void;
}

export interface CallDeps {
  api: CallApi;
  /** Rejects with a DOMException named NotAllowedError (blocked) or NotFoundError (no microphone). */
  openMic(): Promise<MicHandle>;
  /** Device recognition, or null when the browser has none. */
  createRecognizer(locale: string, onText: (text: string, isFinal: boolean) => void, onBlocked: () => void): Recognizer | null;
  speaker: Speaker;
  now(): number;
  every(ms: number, fn: () => void): () => void;
  after(ms: number, fn: () => void): () => void;
  isOnline(): boolean;
}

export interface CallOptions {
  locale: string;
  name: string;
  retention: 'session' | '30_days' | 'permanent';
  stt: 'device' | 'server';
  speakerOn: boolean;
  /** What a frame sent without words asks ("What do you see?"), in the call's language. */
  lookPrompt: string;
  capabilities?: VoiceCapabilities;
  vad?: Partial<VadOptions>;
}

export interface CallSnapshot {
  phase: CallPhase;
  problem: CallProblem | null;
  muted: boolean;
  speakerOn: boolean;
  startedAt: number | null;
  endedAt: number | null;
  /** What is being heard right now (device recognition), cleared at each turn. */
  caption: string;
  turns: CallTurn[];
  /** End of the caller's speech to the first sound of the reply, for the last reply. */
  lastLatencyMs: number | null;
  bargeIns: number;
  stt: 'device' | 'server' | 'typing';
  cameraOn: boolean;
  autoFrames: boolean;
  /** A frame is waiting to go with the next question. */
  frameQueued: boolean;
  summary: CallSummaryResult | null;
  pendingApprovals: number;
}

const LEVEL_TICK_MS = 50;
const FINAL_WAIT_MS = 1200;

export function classifyError(error: unknown, online: boolean): CallProblem {
  if (isBudgetExceeded(error)) return { kind: 'budget', detail: error.detail ?? undefined };
  if (error instanceof ApiError) {
    if (error.status === 429) return { kind: 'limit' };
    if (error.status === 403) return { kind: 'forbidden' };
    if (error.status === 404 || error.status === 409) return { kind: 'session', detail: error.detail ?? undefined };
    return { kind: 'provider', detail: error.detail ?? undefined };
  }
  if (!online || error instanceof TypeError) return { kind: online ? 'provider' : 'offline' };
  return { kind: 'provider', detail: error instanceof Error ? error.message : undefined };
}

/** Echo of the reply's own sentence, as the recognizer heard it, is not the caller talking. */
export function soundsLikeEcho(heard: string, spoken: string): boolean {
  const words = (value: string) => value.toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, ' ').split(/\s+/).filter(Boolean);
  const said = new Set(words(spoken));
  const got = words(heard);
  if (!got.length) return true;
  const fresh = got.filter((word) => !said.has(word)).length;
  return fresh / got.length < 0.5;
}

export class CallEngine {
  private state: CallSnapshot;
  private listeners = new Set<(snapshot: CallSnapshot) => void>();
  private sessionId: string | null = null;
  private sequence = 0;
  private mic: MicHandle | null = null;
  private recognizer: Recognizer | null = null;
  private recorder: AudioRecorder | null = null;
  private stopTicking: (() => void) | null = null;
  private stopWaiting: (() => void) | null = null;
  private reply: AbortController | null = null;
  private heard = '';
  private turnOpen = false;
  private turnEndedAt = 0;
  private speakingSentence = '';
  private frameSource: (() => Promise<Frame | null>) | null = null;
  private queuedFrame = false;
  private readonly vad;
  private readonly capabilities: VoiceCapabilities;
  private segments = new Map<string, string>();

  private readonly deps: CallDeps;
  private readonly options: CallOptions;

  // No parameter properties: unit tests run this file with Node's type stripping.
  constructor(deps: CallDeps, options: CallOptions) {
    this.deps = deps;
    this.options = options;
    this.vad = createVad({ ...options.vad, ...(options.capabilities ? { endOfTurnMs: options.capabilities.latency_target_ms.end_of_turn } : {}) });
    this.capabilities = options.capabilities ?? DEVICE_ONLY;
    this.state = {
      phase: 'idle', problem: null, muted: false, speakerOn: options.speakerOn, startedAt: null, endedAt: null,
      caption: '', turns: [], lastLatencyMs: null, bargeIns: 0, stt: options.stt, cameraOn: false, autoFrames: false,
      frameQueued: false, summary: null, pendingApprovals: 0,
    };
  }

  get snapshot(): CallSnapshot { return this.state; }

  subscribe(listener: (snapshot: CallSnapshot) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private set(patch: Partial<CallSnapshot>) {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener(this.state);
  }

  private get active() { return !['idle', 'ending', 'ended'].includes(this.state.phase); }

  /** Opens the microphone and the session. Problems (blocked microphone, offline) are reported, not thrown. */
  async start(): Promise<void> {
    if (this.state.phase !== 'idle') return;
    if (!this.deps.isOnline()) { this.set({ problem: { kind: 'offline' } }); return; }
    this.set({ phase: 'connecting', problem: null });
    try {
      this.mic = await this.deps.openMic();
    } catch (error) {
      const name = (error as { name?: string } | null)?.name;
      this.set({ phase: 'idle', problem: { kind: name === 'NotFoundError' ? 'mic-missing' : 'mic-denied' } });
      return;
    }
    try {
      const session = await this.deps.api.createSession(this.options.locale, this.options.retention, this.options.name);
      this.sessionId = session.id;
    } catch (error) {
      this.mic.close();
      this.mic = null;
      this.set({ phase: 'idle', problem: classifyError(error, this.deps.isOnline()) });
      return;
    }
    this.sequence = 0;
    this.startRecognition();
    this.stopTicking = this.deps.every(LEVEL_TICK_MS, () => this.tick());
    this.set({ phase: 'listening', startedAt: this.deps.now() });
  }

  private startRecognition() {
    const server = this.options.stt === 'server' && this.mic?.recorder() !== null;
    if (server) { this.set({ stt: 'server' }); return; }
    this.recognizer = this.deps.createRecognizer(this.options.locale, (text, isFinal) => this.onRecognized(text, isFinal), () => {
      this.set({ problem: { kind: 'mic-denied' } });
    });
    if (!this.recognizer) {
      this.set({ stt: 'typing', problem: { kind: 'typing-only' } });
      return;
    }
    this.set({ stt: 'device' });
    this.recognizer.start();
  }

  // ---- hearing the caller ----

  private tick() {
    if (!this.mic || this.state.muted || !this.active) return;
    const level = this.mic.level();
    if (level < 0) return;
    const playing = this.state.phase === 'speaking';
    const event = this.vad.push(level, this.deps.now(), playing);
    if (event === 'start') this.onSpeechStart();
    else if (event === 'end') void this.onSpeechEnd();
  }

  private onSpeechStart() {
    if (this.state.phase === 'speaking' || this.state.phase === 'thinking') this.interrupt(true);
    if (this.state.phase !== 'listening' && this.state.phase !== 'hearing') return;
    this.turnOpen = true;
    this.stopWaiting?.();
    if (this.state.stt === 'server' && this.mic) {
      this.recorder = this.mic.recorder();
      this.recorder?.start();
    }
    this.set({ phase: 'hearing', problem: this.state.problem?.kind === 'heard-nothing' ? null : this.state.problem });
  }

  private async onSpeechEnd() {
    if (this.state.phase !== 'hearing') return;
    this.turnEndedAt = this.deps.now();
    if (this.state.stt === 'server') {
      const recorder = this.recorder;
      this.recorder = null;
      const clip = await recorder?.stop();
      if (!clip || !this.sessionId) { this.set({ phase: 'listening' }); return; }
      this.set({ phase: 'thinking' });
      try {
        const segment = await this.deps.api.transcribe(this.sessionId, clip.blob, this.sequence++, clip.durationMs);
        await this.submit(segment.text, segment.id);
      } catch (error) {
        const problem = error instanceof ApiError && error.status === 422 ? { kind: 'heard-nothing' as const } : classifyError(error, this.deps.isOnline());
        if ((this.state.phase as CallPhase) === 'thinking') this.set({ phase: 'listening', problem });
      }
      return;
    }
    if (this.heard.trim()) { void this.submit(this.heard); return; }
    // The recognizer's final words can trail the silence a little.
    this.stopWaiting = this.deps.after(FINAL_WAIT_MS, () => {
      if (this.state.phase !== 'hearing') return;
      if (this.heard.trim()) void this.submit(this.heard);
      else { this.turnOpen = false; this.set({ phase: 'listening', caption: '', problem: { kind: 'heard-nothing' } }); }
    });
  }

  private onRecognized(text: string, isFinal: boolean) {
    if (!this.active || this.state.muted) return;
    if (this.state.phase === 'speaking') {
      // The recognizer also hears the reply. Words that are not the reply mean the caller is talking over it.
      if (text.trim().split(/\s+/).length >= 3 && !soundsLikeEcho(text, this.speakingSentence)) {
        this.interrupt(true);
        this.recognizer?.mark();
      }
      return;
    }
    if (this.state.phase === 'thinking') return;
    this.heard = text;
    const vadSilent = !this.vad.speaking;
    if (this.state.phase === 'listening' && text.trim()) { this.turnOpen = true; this.set({ phase: 'hearing' }); }
    this.set({ caption: text });
    if (isFinal && text.trim() && vadSilent && this.state.phase === 'hearing') {
      this.turnEndedAt = this.deps.now();
      void this.submit(text);
    }
  }

  // ---- answering ----

  /** A typed message, or a question that the server already transcribed (`segmentId`). */
  async submit(text: string, segmentId?: string): Promise<void> {
    const clean = text.trim() || (this.queuedFrame ? this.options.lookPrompt : '');
    if (!clean || !this.sessionId || !this.active) return;
    this.stopWaiting?.();
    this.turnOpen = false;
    this.heard = '';
    this.recognizer?.mark();
    this.vad.reset();
    if (!this.turnEndedAt) this.turnEndedAt = this.deps.now();
    const useFrame = Boolean(this.frameSource) && (this.queuedFrame || this.state.autoFrames);
    this.queuedFrame = false;
    const abort = new AbortController();
    this.reply?.abort();
    this.reply = abort;
    const you: CallTurn = { id: `you-${this.deps.now()}-${this.sequence}`, speaker: 'you', text: clean, withFrame: useFrame };
    const pendingId = `assistant-${this.deps.now()}-${this.sequence}`;
    this.set({ phase: 'thinking', caption: '', frameQueued: false, problem: null, turns: [...this.state.turns, you] });

    const queue: string[] = [];
    let buffered = '';
    let speaking: Promise<void> | null = null;
    let firstSound = false;
    const markLatency = () => {
      if (firstSound) return;
      firstSound = true;
      this.set({ lastLatencyMs: Math.max(0, this.deps.now() - this.turnEndedAt) });
    };
    const drain = () => {
      if (!this.state.speakerOn || speaking || !queue.length || abort.signal.aborted) return;
      speaking = (async () => {
        while (queue.length && !abort.signal.aborted && this.state.speakerOn) {
          const sentence = queue.shift()!;
          this.speakingSentence = sentence;
          if (this.state.phase === 'thinking') this.set({ phase: 'speaking' });
          await this.deps.speaker.speak(sentence, markLatency);
        }
        this.speakingSentence = '';
        speaking = null;
      })();
    };
    const hear = (final: boolean) => {
      const split = takeSentences(buffered, final);
      buffered = split.rest;
      queue.push(...split.sentences);
      drain();
    };
    const handlers: StreamHandlers = {
      onMeta: (meta) => {
        this.set({ pendingApprovals: meta.workspace.pending_approvals, turns: [...this.state.turns, { id: pendingId, speaker: 'assistant', text: '', meta }] });
      },
      onText: (piece) => {
        if (abort.signal.aborted) return;
        if (!this.state.speakerOn) markLatency();
        buffered += piece;
        this.set({ turns: this.state.turns.map((turn) => turn.id === pendingId ? { ...turn, text: turn.text + piece } : turn) });
        hear(false);
      },
    };
    try {
      const segment = segmentId ?? (await this.deps.api.appendTranscript(this.sessionId, clean, this.sequence++)).id;
      this.segments.set(you.id, segment);
      const frame = useFrame && this.frameSource ? await this.frameSource() : null;
      if (frame) await this.deps.api.look(this.sessionId, segment, frame, abort.signal, handlers);
      else {
        if (useFrame) this.set({ turns: this.state.turns.map((turn) => turn.id === you.id ? { ...turn, withFrame: false } : turn) });
        await this.deps.api.ask(this.sessionId, segment, abort.signal, handlers);
      }
      hear(true);
      while (speaking) await speaking;
      if (abort.signal.aborted) return;
      this.set({ phase: this.state.phase === 'ending' || this.state.phase === 'ended' ? this.state.phase : 'listening' });
    } catch (error) {
      if (abort.signal.aborted) return;
      this.set({
        phase: this.active ? 'listening' : this.state.phase,
        turns: this.state.turns.filter((turn) => turn.id !== pendingId || turn.text),
        problem: classifyError(error, this.deps.isOnline()),
      });
    } finally {
      if (this.reply === abort) this.reply = null;
      this.turnEndedAt = 0;
    }
  }

  /** Stops the reply (the caller talked over it, or tapped). */
  interrupt(bargeIn = false): void {
    if (this.state.phase !== 'speaking' && this.state.phase !== 'thinking') return;
    this.reply?.abort();
    this.reply = null;
    this.deps.speaker.stop();
    this.speakingSentence = '';
    this.heard = '';
    this.recognizer?.mark();
    this.set({ phase: 'listening', bargeIns: this.state.bargeIns + (bargeIn ? 1 : 0) });
  }

  // ---- controls ----

  setMuted(muted: boolean): void {
    if (muted === this.state.muted) return;
    this.mic?.setEnabled(!muted);
    if (muted) {
      this.recognizer?.stop();
      this.vad.reset();
      this.stopWaiting?.();
      this.heard = '';
      this.set({ muted, caption: '', phase: this.state.phase === 'hearing' ? 'listening' : this.state.phase });
    } else {
      this.recognizer?.start();
      this.recognizer?.mark();
      this.set({ muted });
    }
  }

  setSpeaker(on: boolean): void {
    if (!on) this.deps.speaker.stop();
    this.set({ speakerOn: on, phase: !on && this.state.phase === 'speaking' ? 'thinking' : this.state.phase });
  }

  /** The camera is on (frames can be taken) or off. Turning it off drops any queued frame. */
  setCamera(source: (() => Promise<Frame | null>) | null): void {
    this.frameSource = source;
    if (!source) this.queuedFrame = false;
    this.set({ cameraOn: Boolean(source), frameQueued: source ? this.state.frameQueued : false, autoFrames: source ? this.state.autoFrames : false });
  }

  setAutoFrames(on: boolean): void { this.set({ autoFrames: Boolean(this.frameSource) && on }); }

  /** Sends a frame now: with what is being said, or on its own with the look prompt. */
  sendFrame(): void {
    if (!this.frameSource || !this.active) return;
    this.queuedFrame = true;
    this.set({ frameQueued: true });
    if (this.state.phase === 'listening' && !this.turnOpen) void this.submit('');
  }

  reportProblem(problem: CallProblem | null): void { this.set({ problem }); }

  setOnline(online: boolean): void {
    if (!online) this.set({ problem: { kind: 'offline' } });
    else if (this.state.problem?.kind === 'offline') this.set({ problem: null });
  }

  async confirmTask(turnId: string): Promise<string | null> {
    const turn = this.state.turns.find((item) => item.id === turnId);
    if (!turn?.meta?.proposed_task || !this.sessionId) return null;
    try {
      const segment = await this.deps.api.appendTranscript(this.sessionId, turn.meta.proposed_task, this.sequence++);
      const created = await this.deps.api.createTask(this.sessionId, segment.id);
      this.set({ turns: this.state.turns.map((item) => item.id === turnId ? { ...item, resolved: 'created' } : item) });
      return created.title;
    } catch (error) {
      this.set({ problem: classifyError(error, this.deps.isOnline()) });
      return null;
    }
  }

  dismiss(turnId: string): void {
    this.set({ turns: this.state.turns.map((item) => item.id === turnId ? { ...item, resolved: 'dismissed' } : item) });
  }

  /** Hangs up: the microphone, recognizer, voice and camera stop at once. Then `finish`. */
  hangUp(): void {
    if (this.state.phase === 'ended' || this.state.phase === 'ending') return;
    this.reply?.abort();
    this.reply = null;
    this.deps.speaker.stop();
    this.stopTicking?.();
    this.stopTicking = null;
    this.stopWaiting?.();
    this.recognizer?.stop();
    this.recognizer = null;
    void this.recorder?.stop();
    this.recorder = null;
    this.mic?.close();
    this.mic = null;
    this.frameSource = null;
    const wasStarted = this.sessionId !== null;
    this.set({ phase: wasStarted ? 'ending' : 'ended', endedAt: this.deps.now(), cameraOn: false, autoFrames: false, frameQueued: false, caption: '' });
  }

  get durationSeconds(): number {
    const { startedAt, endedAt } = this.state;
    if (startedAt === null) return 0;
    return Math.max(0, Math.round(((endedAt ?? this.deps.now()) - startedAt) / 1000));
  }

  get hasConversation(): boolean { return this.state.turns.some((turn) => turn.text.trim()); }

  /** After hanging up: save the summary (as a conversation) or not, then close the session. */
  async finish(save: boolean): Promise<boolean> {
    if (this.state.phase !== 'ending' || !this.sessionId) return this.state.phase === 'ended';
    if (save && this.hasConversation) {
      try {
        const summary = await this.deps.api.summarize(this.sessionId, this.durationSeconds, true);
        this.set({ summary, problem: null });
      } catch (error) {
        const problem = classifyError(error, this.deps.isOnline());
        this.set({ problem: { kind: 'summary', detail: problem.detail } });
        return false;
      }
    }
    try {
      await this.deps.api.complete(this.sessionId);
    } catch {
      // A session-only transcript left open is erased when it is closed or by retention; the call is over either way.
    }
    this.sessionId = null;
    this.set({ phase: 'ended', problem: null });
    return true;
  }

  /** The capabilities this call was started with. */
  get engines(): VoiceCapabilities { return this.capabilities; }
}
