import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import { ArrowUp, Check, Cpu, Globe2, Keyboard, Mic, Radio, Settings2, ShieldAlert, Square, Volume2, VolumeX, X } from 'lucide-react';
import {
  appendTranscript,
  baseRecognitionMode,
  completeVoiceSession,
  createPushToTalk,
  createVoiceSession,
  createWakeListener,
  detectRecognitionMode,
  greeting,
  microphonePermission,
  sanitizeAssistantName,
  hasVoiceServer,
  listSystemVoices,
  naturalVoiceSupports,
  playWakeChime,
  rankVoices,
  speakAndWait,
  stopSpeaking,
  streamVoiceAssistant,
  submitVoiceCommand,
  type RecognitionMode,
  type ReplyVoice,
  type TranscriptRetention,
  type VoiceAskResult,
  type VoiceSession,
  type WakeListener,
} from './lib/voice';
import { Orb3D } from './Orb3D';
import { budgetMessage, isBudgetExceeded } from './lib/errors';
import './voice.css';
import { takeSentences } from './lib/chat';
import { useDeveloperAssist } from './DeveloperAssist';

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

export function VoiceView({ onOpenApprovals, onOpenDeveloper }: { onOpenApprovals?: () => void; onOpenDeveloper?: () => void } = {}) {
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
  // Sized by width and height so the greeting and dock stay above the fold on short laptop screens.
  const [orbSize] = useState(() => Math.round(Math.max(180, Math.min(window.innerWidth < 480 ? 208 : window.innerWidth < 860 ? 248 : 296, window.innerHeight * 0.3))));
  // Hands-free listening never asks for the microphone by itself: it starts on its own only when
  // permission was already granted, otherwise after the user taps "Start listening".
  const [armed, setArmed] = useState(false);
  const [hour] = useState(() => new Date().getHours());
  const [needsTap, setNeedsTap] = useState(false);
  const controller = useRef<PushToTalkController | null>(null);
  const wake = useRef<WakeListener | null>(null);
  const probedLocale = useRef<string | null>(null);
  const sequence = useRef(0);
  const heard = useRef('');
  const followUp = useRef(false);
  const meter = useRef<{ stream: MediaStream; context: AudioContext; frame: number } | null>(null);
  const logEnd = useRef<HTMLLIElement | null>(null);
  const askRef = useRef<(text?: string) => Promise<void>>(async () => undefined);
  // The streamed reply in flight; aborting it stops the model and the spoken queue.
  const replyAbort = useRef<AbortController | null>(null);
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
  // Developer mode by voice (docs/developer-mode.md): confirm first, announce ready pull requests,
  // and never approve by voice; the merge needs a tap on Confirm merge with the hash on screen.
  const developer = useDeveloperAssist({
    source: 'voice',
    onOpenDeveloper,
    announce: (text) => {
      setTurns((current) => [...current, { id: `developer-${Date.now()}`, speaker: 'assistant', text }]);
      if (!muted) void speakAndWait(text, { locale, voice: effectiveVoice });
    },
  });

  useEffect(() => { probedLocale.current = null; setMode(baseRecognitionMode()); store('anum.voice.locale', locale); }, [locale]);
  useEffect(() => { void listSystemVoices().then(setVoices); }, []);
  useEffect(() => { if (turns.length) logEnd.current?.scrollIntoView({ block: 'nearest' }); }, [turns]);
  useEffect(() => () => { replyAbort.current?.abort(); stopMeter(); stopSpeaking(); controller.current?.cancel(); wake.current?.stop(); }, []);

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
    const local = await developer.intercept(text);
    if (local !== null) {
      setTurns((current) => [...current, { id: `developer-${Date.now()}`, speaker: 'assistant', text: local }]);
      if (!muted) { setState('speaking'); await speakAndWait(local, { locale, voice: effectiveVoice }); }
      setState('idle');
      // A question ("Start?") is answered without saying the name again.
      if (!muted && wakeOnRef.current && local.endsWith('Start?')) window.setTimeout(() => void startRef.current(false, true), 150);
      else wake.current?.resume();
      return;
    }
    const abort = new AbortController();
    replyAbort.current?.abort();
    replyAbort.current = abort;
    const pendingId = `assistant-${Date.now()}`;
    // Speak each sentence as soon as it is complete instead of waiting for the whole reply.
    const queue: string[] = [];
    let buffered = '';
    let speaking: Promise<void> | null = null;
    const speakQueue = () => {
      if (muted || speaking || !queue.length || abort.signal.aborted) return;
      setState('speaking');
      speaking = (async () => {
        while (queue.length && !abort.signal.aborted) {
          await speakAndWait(queue.shift()!, {
            locale,
            voice: effectiveVoice,
            onFallback: () => setNotice('The natural voice could not load, so I used your device voice.'),
          });
        }
        speaking = null;
      })();
    };
    const hear = (final: boolean) => {
      const split = takeSentences(buffered, final);
      buffered = split.rest;
      queue.push(...split.sentences);
      speakQueue();
    };
    try {
      const active = await ensureSession();
      const segment = await appendTranscript(active.id, text, sequence.current++);
      setTurns((current) => [...current, { id: pendingId, speaker: 'assistant', text: '' }]);
      const result = await streamVoiceAssistant(active.id, segment.id, abort.signal, {
        onMeta: (meta) => setPendingApprovals(meta.workspace.pending_approvals),
        onText: (piece) => {
          buffered += piece;
          setTurns((current) => current.map((turn) => turn.id === pendingId ? { ...turn, text: turn.text + piece } : turn));
          hear(false);
        },
      });
      setTurns((current) => current.map((turn) => turn.id === pendingId ? { id: result.assistant_segment.id, speaker: 'assistant', text: result.reply, result } : turn));
      hear(true);
      while (speaking) await speaking;
      if (abort.signal.aborted) return;
      setState((current) => current === 'speaking' || current === 'thinking' ? 'idle' : current);
      // Keep the conversation going: after a reply, listen once for a follow-up without the name.
      if (!muted && wakeOnRef.current && result.risk_tier === 'read') window.setTimeout(() => void startRef.current(false, true), 150);
      else wake.current?.resume();
    } catch (error) {
      setTurns((current) => current.filter((turn) => turn.id !== pendingId || turn.text));
      setState('idle');
      wake.current?.resume();
      if (abort.signal.aborted) return;
      setNotice(isBudgetExceeded(error) ? `Monthly model budget reached. ${budgetMessage(error)}` : error instanceof Error ? error.message : 'I could not answer. Check the connection and try again.');
    } finally {
      if (replyAbort.current === abort) replyAbort.current = null;
    }
  }
  askRef.current = ask;

  // Wake name: listen in the background only while switched on; paused while talking or speaking.
  useEffect(() => {
    if (!wakeOn) { setNeedsTap(false); return; }
    let cancelled = false;
    void (async () => {
      const permission = armed ? 'granted' : await microphonePermission();
      if (cancelled) return;
      if (permission === 'denied') { setMicBlocked(true); setNeedsTap(false); return; }
      if (permission !== 'granted') { setNeedsTap(true); return; }
      setNeedsTap(false);
      return ensureMode();
    })().then((activeMode) => {
      if (cancelled || !activeMode) return;
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
  }, [wakeOn, armed, name, locale]);

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
      setNotice(isBudgetExceeded(error) ? `Monthly model budget reached. ${budgetMessage(error)}` : error instanceof Error ? error.message : 'The task could not be created.');
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
    const clean = sanitizeAssistantName(nameDraft);
    if (!clean) { setNameDraft(name); return; }
    setName(clean);
    setNameDraft(clean);
    store('anum.voice.name', clean);
  }

  async function toggleWake() {
    const next = !wakeOn;
    setWakeOn(next);
    // Switching it on is a user action, so the browser may ask for the microphone now.
    setArmed(next);
    store('anum.voice.wake', next ? 'on' : 'off');
    if (next && (await ensureMode()) === 'cloud') {
      setNotice(`While “${name}” is on, your browser's speech service hears the room until you turn it off.`);
    } else if (!next) {
      setNotice('');
    }
  }

  function onOrbClick() {
    if (state === 'listening') stopListening();
    else if (state === 'speaking') { replyAbort.current?.abort(); stopSpeaking(); setState('idle'); wake.current?.resume(); }
    else void startListening();
  }

  function onDraftKey(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void ask(); }
  }
  const orbScale = state === 'listening' ? 1 + level * 0.1 : 1;
  const modeInfo = modeLabel[mode];
  const caption = notice || (state === 'listening' ? (draft || (arabic ? 'تفضل…' : 'Go ahead…')) : state === 'idle' && !needsTap ? idleHint : '');

  return <section className="voiceConsole" data-state={state} aria-label={`${name} voice assistant`}>
    <header className="voiceTopline">
      <span className="voiceBadge" title={modeInfo.hint}>{modeInfo.icon}{modeInfo.text}</span>
      <div className="voiceConsoleMeta">
        <button type="button" className={wakeOn ? 'voicePill on' : 'voicePill'} onClick={() => void toggleWake()} aria-pressed={wakeOn} title={`When on, saying “${name}” wakes the assistant. Audio is only processed while this page is open.`}>
          <Radio size={16} />{wakeOn ? `“${name}” is on` : `Wake on “${name}”`}
        </button>
        <button type="button" className="voiceIconButton" onClick={() => { setMuted(!muted); if (!muted) stopSpeaking(); }} aria-pressed={muted} aria-label={muted ? 'Unmute spoken replies' : 'Mute spoken replies'} title={muted ? 'Spoken replies are off' : 'Spoken replies are on'}>
          {muted ? <VolumeX size={18} /> : <Volume2 size={18} />}
        </button>
      </div>
    </header>

    <div className="voiceStage">
      <button
        type="button"
        className="voiceOrb voiceTalk"
        style={{ transform: `scale(${orbScale})`, width: orbSize, height: orbSize }}
        onClick={onOrbClick}
        disabled={state === 'thinking'}
        aria-label={state === 'listening' ? 'Stop listening' : state === 'speaking' ? 'Stop speaking' : 'Tap to talk'}
      >
        <span className="voiceOrbHalo" aria-hidden="true" />
        <span className="voiceOrbGlow" aria-hidden="true" />
        <Orb3D state={state} alert={pendingApprovals > 0} level={level} size={orbSize} fallback={<><span className="voiceOrbBlob b1" /><span className="voiceOrbBlob b2" /><span className="voiceOrbBlob b3" /></>} />
        <span className="voiceOrbIcon" aria-hidden="true">{state === 'listening' ? <Square size={26} /> : state === 'speaking' ? <VolumeX size={28} /> : <Mic size={30} />}</span>
      </button>
      <h2 className="voiceGreeting">{greeting(name, locale, hour)}</h2>
      <p className="voiceStateLabel" role="status" aria-live="polite">{stateText[state]}</p>
      <p className={state === 'listening' && draft ? 'voiceCaption live' : 'voiceCaption'}>{caption}</p>
      <div className="voiceStageActions">
        {needsTap && !micBlocked && <button type="button" className="voiceAllowMic" onClick={() => setArmed(true)}><Radio size={16} />Start listening for “{name}”</button>}
        {micBlocked && <button type="button" className="voiceAllowMic" onClick={() => { setMicBlocked(false); setNotice(''); setArmed(true); setWakeOn(false); window.setTimeout(() => setWakeOn(true), 0); }}>Allow microphone</button>}
        {pendingApprovals > 0 && <button type="button" className="voiceScope" onClick={onOpenApprovals}>
          {pendingApprovals === 1 ? '1 approval waiting for you' : `${pendingApprovals} approvals waiting for you`}
        </button>}
      </div>
    </div>

    {developer.panel}

    <ol className="voiceLog" aria-label="Conversation">
      {turns.length === 0 && <li className="voiceEmpty">
        <span className="voiceEmptyLead">{arabic ? 'جرّب أن تقول' : 'Try saying'}</span>
        <span>“{arabic ? 'ما اسمك؟' : "What's your name?"}”</span>
        <span>“{arabic ? 'ما حالة مهامي؟' : "What's the status of my workspace?"}”</span>
        <span>“{arabic ? 'أنشئ مهمة: تجهيز التقرير الأسبوعي' : 'Create a task: draft the weekly report'}”</span>
      </li>}
      {turns.filter((turn) => turn.text).map((turn) => <li key={turn.id} className={`voiceTurn ${turn.speaker}`}>
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

    <footer className="voiceDock">
      <button type="button" className="voiceDockMic voiceTalk" onClick={onOrbClick} disabled={state === 'thinking'} aria-label={state === 'listening' ? 'Stop listening' : 'Push to talk'} title="Push to talk (or hold Space)">
        {state === 'listening' ? <Square size={18} /> : <Mic size={18} />}
      </button>
      <label className="voiceDraft">
        <span className="visuallyHidden">Your message</span>
        <textarea value={state === 'listening' ? '' : draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onDraftKey} rows={1} placeholder={arabic ? 'أو اكتب رسالتك هنا' : `Message ${name}`} disabled={state === 'listening'} />
      </label>
      <button type="button" className="voiceAsk" onClick={() => void ask()} disabled={!draft.trim() || state === 'thinking' || state === 'listening'} aria-label="Send" title="Send"><ArrowUp size={18} /></button>
    </footer>

    <details className="voiceSettings">
      <summary><Settings2 size={16} />Voice settings</summary>
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
