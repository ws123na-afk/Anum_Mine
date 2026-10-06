import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import { Check, Cpu, Globe2, Keyboard, Mic, ShieldAlert, Square, Volume2, VolumeX, X } from 'lucide-react';
import {
  appendTranscript,
  askVoiceAssistant,
  completeVoiceSession,
  createPushToTalk,
  createVoiceSession,
  baseRecognitionMode,
  detectRecognitionMode,
  hasVoiceServer,
  listSystemVoices,
  speak,
  stopSpeaking,
  submitVoiceCommand,
  type RecognitionMode,
  type TranscriptRetention,
  type VoiceAskResult,
  type VoiceSession,
} from './lib/voice';

type ConsoleState = 'idle' | 'listening' | 'thinking' | 'speaking';

interface Turn {
  id: string;
  speaker: 'you' | 'anum';
  text: string;
  result?: VoiceAskResult;
  resolved?: 'created' | 'dismissed';
}

interface PushToTalkController {
  supported: boolean;
  start: () => void;
  stop: () => void;
  cancel: () => void;
}

const stateLabel: Record<ConsoleState, string> = {
  idle: 'Ready',
  listening: 'Listening',
  thinking: 'Thinking',
  speaking: 'Speaking',
};

const modeLabel: Record<RecognitionMode, { text: string; icon: ReactNode; hint: string }> = {
  'on-device': { text: 'On-device', icon: <Cpu size={14} />, hint: 'Speech is recognised on this device and never leaves it.' },
  cloud: { text: 'Browser cloud', icon: <Globe2 size={14} />, hint: 'Your browser sends audio to its speech service for recognition.' },
  typing: { text: 'Typing only', icon: <Keyboard size={14} />, hint: 'This browser has no speech recognition. Type your question instead.' },
};

const tierLabel = { read: 'Answer only', confirm: 'Needs your confirmation', visual_only: 'On-screen only' } as const;

export function VoiceView({ onOpenApprovals }: { onOpenApprovals?: () => void } = {}) {
  const [session, setSession] = useState<VoiceSession | null>(null);
  const [locale, setLocale] = useState(() => navigator.language || 'en-US');
  const [retention, setRetention] = useState<TranscriptRetention>('session');
  const [state, setState] = useState<ConsoleState>('idle');
  const [draft, setDraft] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [mode, setMode] = useState<RecognitionMode>(baseRecognitionMode);
  const probedLocale = useRef<string | null>(null);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [voiceName, setVoiceName] = useState('');
  const [muted, setMuted] = useState(false);
  const [notice, setNotice] = useState('Hold the button or the space bar and speak.');
  const [level, setLevel] = useState(0);
  const controller = useRef<PushToTalkController | null>(null);
  const sequence = useRef(0);
  const finalTranscript = useRef('');
  const meter = useRef<{ stream: MediaStream; context: AudioContext; frame: number } | null>(null);
  const logEnd = useRef<HTMLLIElement | null>(null);

  useEffect(() => { probedLocale.current = null; setMode(baseRecognitionMode()); }, [locale]);
  useEffect(() => { void listSystemVoices().then(setVoices); }, []);
  useEffect(() => { logEnd.current?.scrollIntoView({ block: 'nearest' }); }, [turns]);
  useEffect(() => () => { stopMeter(); stopSpeaking(); controller.current?.cancel(); }, []);

  const languageVoices = voices.filter((voice) => voice.lang.toLowerCase().startsWith(locale.slice(0, 2).toLowerCase()));

  async function ensureSession(): Promise<VoiceSession> {
    if (session && session.locale === locale) return session;
    const created = await createVoiceSession(locale, retention);
    setSession(created);
    sequence.current = 0;
    return created;
  }

  async function startMeter() {
    if (!navigator.mediaDevices?.getUserMedia) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const context = new AudioContext();
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      context.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteFrequencyData(data);
        const average = data.reduce((sum, value) => sum + value, 0) / data.length;
        setLevel(Math.min(1, average / 90));
        if (meter.current) meter.current.frame = requestAnimationFrame(tick);
      };
      meter.current = { stream, context, frame: requestAnimationFrame(tick) };
    } catch {
      // The level meter is decorative; recognition still works without it.
    }
  }

  function stopMeter() {
    if (!meter.current) return;
    cancelAnimationFrame(meter.current.frame);
    meter.current.stream.getTracks().forEach((track) => track.stop());
    void meter.current.context.close();
    meter.current = null;
    setLevel(0);
  }

  const startListening = useCallback(async () => {
    if (state === 'listening' || state === 'thinking') return;
    stopSpeaking();
    if (mode === 'typing') {
      setNotice(modeLabel.typing.hint);
      return;
    }
    let activeMode: RecognitionMode = mode;
    if (probedLocale.current !== locale) {
      probedLocale.current = locale;
      activeMode = await detectRecognitionMode(locale);
      setMode(activeMode);
    }
    finalTranscript.current = '';
    setDraft('');
    controller.current = createPushToTalk(
      (text) => { finalTranscript.current = text; setDraft(text); },
      (message) => { setNotice(message); setState('idle'); stopMeter(); },
      locale,
      undefined,
      activeMode === 'on-device',
    );
    controller.current.start();
    setState('listening');
    setNotice('Listening. Release to review what you said.');
    void startMeter();
  }, [locale, mode, state]);

  const stopListening = useCallback(() => {
    if (state !== 'listening') return;
    controller.current?.stop();
    stopMeter();
    setState('idle');
    setNotice(finalTranscript.current ? 'Check the words, then press Ask.' : 'Nothing was heard. Try again or type.');
  }, [state]);

  useEffect(() => {
    // Space keeps its normal meaning on form fields and other controls; only the talk button and the page use push-to-talk.
    const isTyping = (target: EventTarget | null) =>
      target instanceof HTMLElement
      && ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A', 'SUMMARY'].includes(target.tagName)
      && !target.classList.contains('voiceTalk');
    const down = (event: KeyboardEvent) => {
      if (event.code === 'Space' && !event.repeat && !isTyping(event.target)) { event.preventDefault(); void startListening(); }
    };
    const up = (event: KeyboardEvent) => {
      if (event.code === 'Space' && !isTyping(event.target)) { event.preventDefault(); stopListening(); }
    };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); };
  }, [startListening, stopListening]);

  async function ask() {
    const text = draft.trim();
    if (!text || state === 'thinking') return;
    setState('thinking');
    setNotice('Thinking...');
    const youTurn: Turn = { id: `you-${Date.now()}`, speaker: 'you', text };
    setTurns((current) => [...current, youTurn]);
    setDraft('');
    try {
      const active = await ensureSession();
      const segment = await appendTranscript(active.id, text, sequence.current++);
      const result = await askVoiceAssistant(active.id, segment.id);
      setTurns((current) => [...current, { id: result.assistant_segment.id, speaker: 'anum', text: result.reply, result }]);
      setNotice(result.risk_tier === 'confirm' ? 'Confirm on screen to create the task.' : 'Hold to ask another question.');
      if (muted) {
        setState('idle');
      } else {
        setState('speaking');
        // Some browsers have no voices and never fire onend; never leave the console stuck.
        const fallback = window.setTimeout(() => setState((current) => current === 'speaking' ? 'idle' : current), Math.min(20000, 1500 + result.reply.length * 70));
        await speak(result.reply, {
          locale,
          voiceName: voiceName || undefined,
          onEnd: () => { window.clearTimeout(fallback); setState('idle'); },
        });
      }
    } catch (error) {
      setState('idle');
      setNotice(error instanceof Error ? error.message : 'ANUM could not answer. Check the connection and try again.');
    }
  }

  async function confirmTask(turn: Turn) {
    if (!session || !turn.result?.proposed_task) return;
    try {
      const segment = await appendTranscript(session.id, turn.result.proposed_task, sequence.current++);
      const created = await submitVoiceCommand(session.id, segment.id);
      setTurns((current) => current.map((item) => item.id === turn.id ? { ...item, resolved: 'created' } : item));
      setNotice(`Task created: ${created.task.title}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'The task could not be created.');
    }
  }

  function dismiss(turn: Turn) {
    setTurns((current) => current.map((item) => item.id === turn.id ? { ...item, resolved: 'dismissed' } : item));
  }

  async function endSession() {
    stopSpeaking();
    if (session?.status === 'active') {
      try { await completeVoiceSession(session.id); } catch { /* session expires server-side */ }
    }
    setSession(null);
    setTurns([]);
    setNotice('Session ended. Transcripts follow your retention choice.');
  }

  function onDraftKey(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void ask(); }
  }

  const orbScale = state === 'listening' ? 1 + level * 0.35 : 1;
  const modeInfo = modeLabel[mode];

  return <section className="voiceConsole" data-state={state} aria-label="ANUM voice assistant">
    <header className="voiceConsoleHeader">
      <div>
        <p className="eyebrow">Voice assistant</p>
        <h2>Ask ANUM</h2>
      </div>
      <div className="voiceConsoleMeta">
        <span className="voiceBadge" title={modeInfo.hint}>{modeInfo.icon}{modeInfo.text}</span>
        <button type="button" className="voiceIconButton" onClick={() => { setMuted(!muted); if (!muted) stopSpeaking(); }} aria-pressed={muted} aria-label={muted ? 'Unmute spoken replies' : 'Mute spoken replies'}>
          {muted ? <VolumeX size={18} /> : <Volume2 size={18} />}
        </button>
      </div>
    </header>

    <div className="voiceStage">
      <div className="voiceOrbWrap">
        <div className="voiceOrb" style={{ transform: `scale(${orbScale})` }} aria-hidden="true">
          <span className="voiceOrbRing r1" /><span className="voiceOrbRing r2" /><span className="voiceOrbRing r3" /><span className="voiceOrbCore" />
        </div>
        <p className="voiceStateLabel" role="status" aria-live="polite">{stateLabel[state]}</p>
        <p className="voiceNotice">{notice}</p>
      </div>

      <ol className="voiceLog" aria-label="Conversation">
        {turns.length === 0 && <li className="voiceEmpty">Try “What’s the status of my workspace?” or “Create a task: draft the weekly report”.</li>}
        {turns.map((turn) => <li key={turn.id} className={`voiceTurn ${turn.speaker}`}>
          <span className="voiceSpeaker">{turn.speaker === 'you' ? 'You' : 'ANUM'}</span>
          <p>{turn.text}</p>
          {turn.result && <span className={`voiceTier ${turn.result.risk_tier}`}>{tierLabel[turn.result.risk_tier]}</span>}
          {turn.result?.risk_tier === 'confirm' && turn.result.proposed_task && !turn.resolved && <div className="voiceConfirm">
            <button type="button" onClick={() => void confirmTask(turn)}><Check size={16} />Create task</button>
            <button type="button" className="secondary" onClick={() => dismiss(turn)}><X size={16} />Not now</button>
          </div>}
          {turn.resolved === 'created' && <span className="voiceResolved">Task created</span>}
          {turn.result?.risk_tier === 'visual_only' && onOpenApprovals && <div className="voiceConfirm">
            <button type="button" className="secondary" onClick={onOpenApprovals}><ShieldAlert size={16} />Open Approvals</button>
          </div>}
        </li>)}
        <li ref={logEnd} aria-hidden="true" className="voiceLogEnd" />
      </ol>
    </div>

    <footer className="voiceDock">
      <button
        type="button"
        className={state === 'listening' ? 'voiceTalk active' : 'voiceTalk'}
        onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); void startListening(); }}
        onPointerUp={stopListening}
        onPointerCancel={stopListening}
        disabled={state === 'thinking'}
        aria-label={state === 'listening' ? 'Release to stop listening' : 'Hold to talk'}
      >
        {state === 'listening' ? <Square size={24} /> : <Mic size={26} />}
      </button>
      <label className="voiceDraft">
        <span className="visuallyHidden">Your question</span>
        <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onDraftKey} rows={2} placeholder="Hold to talk, or type a question" />
      </label>
      <button type="button" className="voiceAsk" onClick={() => void ask()} disabled={!draft.trim() || state === 'thinking' || state === 'listening'}>Ask</button>
    </footer>

    <details className="voiceSettings">
      <summary>Voice settings</summary>
      <div className="voiceSettingsGrid">
        <label className="field"><span>Language</span><select value={locale} onChange={(event) => { setLocale(event.target.value); setVoiceName(''); }} disabled={state !== 'idle'}>
          <option value="en-US">English (US)</option><option value="en-GB">English (UK)</option><option value="ar-SA">العربية (السعودية)</option><option value="ar-AE">العربية (الإمارات)</option>
          {!['en-US', 'en-GB', 'ar-SA', 'ar-AE'].includes(locale) && <option value={locale}>{locale}</option>}
        </select></label>
        <label className="field"><span>Reply voice</span><select value={voiceName} onChange={(event) => setVoiceName(event.target.value)}>
          <option value="">Automatic</option>
          {hasVoiceServer() && <option value="server">Self-hosted voice (consented clone)</option>}
          {languageVoices.map((voice) => <option key={voice.name} value={voice.name}>{voice.name}</option>)}
        </select></label>
        <label className="field"><span>Transcript retention</span><select value={retention} onChange={(event) => setRetention(event.target.value as TranscriptRetention)} disabled={Boolean(session)}>
          <option value="session">Delete when session ends</option><option value="30_days">Keep for 30 days</option><option value="permanent">Keep until deleted</option>
        </select></label>
      </div>
      <p className="muted">Approvals, deletions and payments are never carried out by voice. ANUM tells you to confirm them on screen.</p>
      {session && <button type="button" className="secondary" onClick={() => void endSession()}>End voice session</button>}
    </details>
  </section>;
}
