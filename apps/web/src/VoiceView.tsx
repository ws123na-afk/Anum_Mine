import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import { Check, Cpu, Globe2, Keyboard, Mic, Radio, ShieldAlert, Square, Volume2, VolumeX, X } from 'lucide-react';
import {
  appendTranscript,
  askVoiceAssistant,
  baseRecognitionMode,
  completeVoiceSession,
  createPushToTalk,
  createVoiceSession,
  createWakeListener,
  detectRecognitionMode,
  hasVoiceServer,
  listSystemVoices,
  naturalVoiceSupports,
  playWakeChime,
  rankVoices,
  speak,
  stopSpeaking,
  submitVoiceCommand,
  type RecognitionMode,
  type ReplyVoice,
  type TranscriptRetention,
  type VoiceAskResult,
  type VoiceSession,
  type WakeListener,
} from './lib/voice';
import { Orb3D } from './Orb3D';

type ConsoleState = 'idle' | 'listening' | 'thinking' | 'speaking';

interface Turn {
  id: string;
  speaker: 'you' | 'assistant';
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

const modeLabel: Record<RecognitionMode, { text: string; icon: ReactNode; hint: string }> = {
  'on-device': { text: 'On-device', icon: <Cpu size={14} />, hint: 'Speech is recognised on this device and never leaves it.' },
  cloud: { text: 'Browser cloud', icon: <Globe2 size={14} />, hint: 'Your browser sends audio to its speech service for recognition.' },
  typing: { text: 'Typing only', icon: <Keyboard size={14} />, hint: 'This browser has no speech recognition. Type your message instead.' },
};

const tierLabel = { read: 'Answer', confirm: 'Needs your OK', visual_only: 'On screen only' } as const;

function stored(key: string, fallback: string): string {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function store(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* preferences are optional */ }
}

export function VoiceView({ onOpenApprovals }: { onOpenApprovals?: () => void } = {}) {
  const [session, setSession] = useState<VoiceSession | null>(null);
  const [locale, setLocale] = useState(() => stored('anum.voice.locale', navigator.language || 'en-US'));
  const [name, setName] = useState(() => stored('anum.voice.name', 'Anum'));
  const [nameDraft, setNameDraft] = useState(name);
  const [wakeOn, setWakeOn] = useState(() => stored('anum.voice.wake', 'on') === 'on');
  const [micBlocked, setMicBlocked] = useState(false);
  const [retention, setRetention] = useState<TranscriptRetention>('session');
  const [state, setState] = useState<ConsoleState>('idle');
  const [draft, setDraft] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [mode, setMode] = useState<RecognitionMode>(baseRecognitionMode);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [voice, setVoice] = useState<ReplyVoice>(() => stored('anum.voice.reply', 'natural'));
  const [muted, setMuted] = useState(false);
  const [notice, setNotice] = useState('');
  const [level, setLevel] = useState(0);
  const [pendingApprovals, setPendingApprovals] = useState(0);
  const [orbSize] = useState(() => (window.innerWidth < 860 ? 170 : 220));
  const controller = useRef<PushToTalkController | null>(null);
  const wake = useRef<WakeListener | null>(null);
  const probedLocale = useRef<string | null>(null);
  const sequence = useRef(0);
  const heard = useRef('');
  const followUp = useRef(false);
  const meter = useRef<{ stream: MediaStream; context: AudioContext; frame: number } | null>(null);
  const logEnd = useRef<HTMLLIElement | null>(null);
  const askRef = useRef<(text?: string) => Promise<void>>(async () => undefined);
  const startRef = useRef<(holdToTalk?: boolean, followUpTurn?: boolean) => Promise<void>>(async () => undefined);

  const wakeOnRef = useRef(wakeOn);
  wakeOnRef.current = wakeOn;
  const arabic = locale.toLowerCase().startsWith('ar');
  const idleHint = wakeOn
    ? (arabic ? `قل "${name}" أو اضغط الميكروفون.` : `Say “${name}” or tap the mic.`)
    : (arabic ? 'اضغط الميكروفون وتحدث.' : 'Tap the mic and just talk.');
  const stateText: Record<ConsoleState, string> = {
    idle: wakeOn ? `Listening for “${name}”` : 'Ready',
    listening: "I'm listening",
    thinking: 'One moment',
    speaking: `${name} is speaking`,
  };
  const rankedVoices = rankVoices(voices, locale);
  const effectiveVoice: ReplyVoice = voice === 'natural' && !naturalVoiceSupports(locale) ? '' : voice;

  useEffect(() => { probedLocale.current = null; setMode(baseRecognitionMode()); store('anum.voice.locale', locale); }, [locale]);
  useEffect(() => { void listSystemVoices().then(setVoices); }, []);
  useEffect(() => { logEnd.current?.scrollIntoView({ block: 'nearest' }); }, [turns]);
  useEffect(() => () => { stopMeter(); stopSpeaking(); controller.current?.cancel(); wake.current?.stop(); }, []);

  async function ensureMode(): Promise<RecognitionMode> {
    if (probedLocale.current === locale) return mode;
    probedLocale.current = locale;
    const detected = await detectRecognitionMode(locale);
    setMode(detected);
    return detected;
  }

  async function ensureSession(): Promise<VoiceSession> {
    if (session && session.locale === locale && session.assistant_name === name && session.status === 'active') return session;
    const created = await createVoiceSession(locale, retention, name);
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

  async function startListening(holdToTalk = false, followUpTurn = false) {
    if (state === 'listening' || state === 'thinking') return;
    followUp.current = followUpTurn;
    stopSpeaking();
    const activeMode = await ensureMode();
    if (activeMode === 'typing') { setNotice(modeLabel.typing.hint); return; }
    wake.current?.pause();
    heard.current = '';
    setDraft('');
    controller.current = createPushToTalk(
      (text) => { heard.current = text; setDraft(text); },
      (message) => { setNotice(message); setState('idle'); stopMeter(); wake.current?.resume(); },
      locale,
      () => {
        // The browser ends the turn when you pause; send what was heard straight away.
        stopMeter();
        const text = heard.current.trim();
        if (text) void askRef.current(text);
        else {
          setState('idle');
          // A silent follow-up window just hands back to listening for the name.
          if (!followUp.current) setNotice(arabic ? 'لم أسمع شيئًا. حاول مرة أخرى.' : "I didn't catch that. Try again?");
          wake.current?.resume();
        }
      },
      activeMode === 'on-device',
      holdToTalk,
    );
    controller.current.start();
    setState('listening');
    setNotice('');
    void startMeter();
  }
  startRef.current = startListening;

  const stopListening = useCallback(() => { controller.current?.stop(); }, []);

  async function ask(spoken?: string) {
    const text = (spoken ?? draft).trim();
    if (!text) { setState('idle'); return; }
    wake.current?.pause();
    setState('thinking');
    setNotice('');
    setTurns((current) => [...current, { id: `you-${Date.now()}`, speaker: 'you', text }]);
    setDraft('');
    try {
      const active = await ensureSession();
      const segment = await appendTranscript(active.id, text, sequence.current++);
      const result = await askVoiceAssistant(active.id, segment.id);
      setTurns((current) => [...current, { id: result.assistant_segment.id, speaker: 'assistant', text: result.reply, result }]);
      setPendingApprovals(result.workspace.pending_approvals);
      if (muted) { setState('idle'); wake.current?.resume(); return; }
      // finish() may run from a timer after state changes; read the latest wake setting via a ref.
      setState('speaking');
      const finish = () => {
        setState((current) => current === 'speaking' ? 'idle' : current);
        // Keep the conversation going: after a reply, listen once for a follow-up without the name.
        if (wakeOnRef.current && result.risk_tier === 'read') window.setTimeout(() => void startRef.current(false, true), 150);
        else wake.current?.resume();
      };
      // Some browsers never report the end of speech; never leave the console stuck.
      const fallback = window.setTimeout(finish, Math.min(30000, 4000 + result.reply.length * 80));
      await speak(result.reply, {
        locale,
        voice: effectiveVoice,
        onFallback: () => setNotice('The natural voice could not load, so I used your device voice.'),
        onEnd: () => { window.clearTimeout(fallback); finish(); },
      });
    } catch (error) {
      setState('idle');
      wake.current?.resume();
      setNotice(error instanceof Error ? error.message : 'I could not answer. Check the connection and try again.');
    }
  }
  askRef.current = ask;

  // Wake name: listen in the background only while switched on; paused while talking or speaking.
  useEffect(() => {
    if (!wakeOn) return;
    let cancelled = false;
    void ensureMode().then((activeMode) => {
      if (cancelled) return;
      if (activeMode === 'typing') { setNotice(modeLabel.typing.hint); return; }
      wake.current = createWakeListener(name, locale, activeMode === 'on-device', (rest) => {
        playWakeChime();
        if (rest) void askRef.current(rest);
        else void startRef.current();
      }, (message) => { setNotice(message); setMicBlocked(true); });
      wake.current?.start();
      setMicBlocked(false);
    });
    return () => { cancelled = true; wake.current?.stop(); wake.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wakeOn, name, locale]);

  useEffect(() => {
    const isOtherControl = (target: EventTarget | null) =>
      target instanceof HTMLElement
      && ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A', 'SUMMARY'].includes(target.tagName)
      && !target.classList.contains('voiceTalk');
    const down = (event: KeyboardEvent) => {
      if (event.code === 'Space' && !event.repeat && !isOtherControl(event.target)) { event.preventDefault(); void startRef.current(true); }
    };
    const up = (event: KeyboardEvent) => {
      if (event.code === 'Space' && !isOtherControl(event.target)) { event.preventDefault(); stopListening(); }
    };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); };
  }, [stopListening]);

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
    setNotice('Conversation cleared. Transcripts follow your retention choice.');
  }

  function saveName() {
    const clean = nameDraft.trim().replace(/[^\p{L}\p{N} .'-]/gu, '').slice(0, 40);
    if (!clean) { setNameDraft(name); return; }
    setName(clean);
    setNameDraft(clean);
    store('anum.voice.name', clean);
  }

  async function toggleWake() {
    const next = !wakeOn;
    setWakeOn(next);
    store('anum.voice.wake', next ? 'on' : 'off');
    if (next && (await ensureMode()) === 'cloud') {
      setNotice(`While “${name}” is on, your browser's speech service hears the room until you turn it off.`);
    } else if (!next) {
      setNotice('');
    }
  }

  function onOrbClick() {
    if (state === 'listening') stopListening();
    else if (state === 'speaking') { stopSpeaking(); setState('idle'); wake.current?.resume(); }
    else void startListening();
  }

  function onDraftKey(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void ask(); }
  }

  const orbScale = state === 'listening' ? 1 + level * 0.12 : 1;
  const modeInfo = modeLabel[mode];

  return <section className="voiceConsole" data-state={state} aria-label={`${name} voice assistant`}>
    <header className="voiceConsoleHeader">
      <div className="voiceIdentity">
        <span className="voiceAvatar" aria-hidden="true">{name.slice(0, 1).toUpperCase()}</span>
        <div><h2>{name}</h2><p>{arabic ? 'مساعدتك الصوتية' : 'Your voice assistant'}</p></div>
      </div>
      <div className="voiceConsoleMeta">
        <span className="voiceBadge" title={modeInfo.hint}>{modeInfo.icon}{modeInfo.text}</span>
        <button type="button" className={wakeOn ? 'voicePill on' : 'voicePill'} onClick={() => void toggleWake()} aria-pressed={wakeOn} title={`When on, saying “${name}” wakes the assistant. Audio is only processed while this page is open.`}>
          <Radio size={14} />{wakeOn ? `“${name}” is on` : `Wake on “${name}”`}
        </button>
        <button type="button" className="voiceIconButton" onClick={() => { setMuted(!muted); if (!muted) stopSpeaking(); }} aria-pressed={muted} aria-label={muted ? 'Unmute spoken replies' : 'Mute spoken replies'}>
          {muted ? <VolumeX size={18} /> : <Volume2 size={18} />}
        </button>
      </div>
    </header>

    <div className="voiceStage">
      <div className="voiceOrbWrap">
        <button
          type="button"
          className="voiceOrb voiceTalk"
          style={{ transform: `scale(${orbScale})`, width: orbSize, height: orbSize }}
          onClick={onOrbClick}
          disabled={state === 'thinking'}
          aria-label={state === 'listening' ? 'Stop listening' : state === 'speaking' ? 'Stop speaking' : 'Tap to talk'}
        >
          <span className="voiceOrbGlow" />
          <Orb3D state={state} alert={pendingApprovals > 0} level={level} size={orbSize} fallback={<><span className="voiceOrbBlob b1" /><span className="voiceOrbBlob b2" /><span className="voiceOrbBlob b3" /></>} />
          <span className="voiceOrbIcon">{state === 'listening' ? <Square size={24} /> : <Mic size={30} />}</span>
        </button>
        <p className="voiceStateLabel" role="status" aria-live="polite">{stateText[state]}</p>
        <p className="voiceNotice">{notice || (state === 'idle' ? idleHint : state === 'listening' ? (draft || '…') : '')}</p>
        {pendingApprovals > 0 && <button type="button" className="voiceScope" onClick={onOpenApprovals}>
          {pendingApprovals === 1 ? '1 approval waiting for you' : `${pendingApprovals} approvals waiting for you`}
        </button>}
        {micBlocked && <button type="button" className="voiceAllowMic" onClick={() => { setMicBlocked(false); setNotice(''); setWakeOn(false); window.setTimeout(() => setWakeOn(true), 0); }}>Allow microphone</button>}
      </div>

      <ol className="voiceLog" aria-label="Conversation">
        {turns.length === 0 && <li className="voiceEmpty">
          <strong>{arabic ? 'جرّب أن تقول' : 'Try saying'}</strong>
          <span>“{arabic ? 'ما اسمك؟' : "What's your name?"}”</span>
          <span>“{arabic ? 'ما حالة مهامي؟' : "What's the status of my workspace?"}”</span>
          <span>“{arabic ? 'أنشئ مهمة: تجهيز التقرير الأسبوعي' : 'Create a task: draft the weekly report'}”</span>
        </li>}
        {turns.map((turn) => <li key={turn.id} className={`voiceTurn ${turn.speaker}`}>
          {turn.speaker === 'assistant' && <span className="voiceSpeaker">{name}</span>}
          <p>{turn.text}</p>
          {turn.result && turn.result.risk_tier !== 'read' && <span className={`voiceTier ${turn.result.risk_tier}`}>{tierLabel[turn.result.risk_tier]}</span>}
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
      <label className="voiceDraft">
        <span className="visuallyHidden">Your message</span>
        <textarea value={state === 'listening' ? '' : draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onDraftKey} rows={1} placeholder={arabic ? 'أو اكتب رسالتك هنا' : 'Or type a message'} disabled={state === 'listening'} />
      </label>
      <button type="button" className="voiceAsk" onClick={() => void ask()} disabled={!draft.trim() || state === 'thinking' || state === 'listening'}>Send</button>
    </footer>

    <details className="voiceSettings">
      <summary>Voice settings</summary>
      <div className="voiceSettingsGrid">
        <label className="field"><span>Assistant name</span>
          <input value={nameDraft} maxLength={40} onChange={(event) => setNameDraft(event.target.value)} onBlur={saveName} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); saveName(); } }} />
        </label>
        <label className="field"><span>Language</span><select value={locale} onChange={(event) => setLocale(event.target.value)} disabled={state !== 'idle'}>
          <option value="en-US">English (US)</option><option value="en-GB">English (UK)</option><option value="ar-SA">العربية (السعودية)</option><option value="ar-AE">العربية (الإمارات)</option>
          {!['en-US', 'en-GB', 'ar-SA', 'ar-AE'].includes(locale) && <option value={locale}>{locale}</option>}
        </select></label>
        <label className="field"><span>Reply voice</span><select value={voice} onChange={(event) => { setVoice(event.target.value); store('anum.voice.reply', event.target.value); }}>
          <option value="natural">Natural (free, English, downloads once)</option>
          <option value="">Best voice on this device</option>
          {hasVoiceServer() && <option value="server">Self-hosted voice (consented clone)</option>}
          {rankedVoices.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
        </select></label>
        <label className="field"><span>Transcript retention</span><select value={retention} onChange={(event) => setRetention(event.target.value as TranscriptRetention)} disabled={Boolean(session)}>
          <option value="session">Delete when session ends</option><option value="30_days">Keep for 30 days</option><option value="permanent">Keep until deleted</option>
        </select></label>
      </div>
      {voice === 'natural' && !naturalVoiceSupports(locale) && <p className="muted">The natural voice speaks English only, so Arabic replies use the best Arabic voice on this device.</p>}
      <p className="muted">{name} never approves, deletes or pays by voice. Those always need your tap on screen.</p>
      {session && <button type="button" className="secondary" onClick={() => void endSession()}>Clear conversation</button>}
    </details>
  </section>;
}
