import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import { ArrowUp, Check, Cpu, Globe2, Keyboard, Maximize2, Mic, Radio, Settings2, ShieldAlert, Square, Volume2, VolumeX, X } from 'lucide-react';
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
  outputAmplitude,
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
import { OrbFullscreen } from './OrbFullscreen';
import { SpaceOrb } from './SpaceOrb';
import { voiceSpaceMode } from './lib/spaceMode';
import { budgetMessage, isBudgetExceeded } from './lib/errors';
import './voice.css';
import { takeSentences } from './lib/chat';
import { CopyAction } from './MessageActions';
import { useDeveloperAssist } from './DeveloperAssist';
import { getLocale, t, translate, type MessageKey } from './lib/i18n';

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

// The interface follows the page language (Settings > Language); the speech language below only
// picks what the browser listens for and how replies are spoken (docs/web-localization.md).
const modeKeys: Record<RecognitionMode, { text: MessageKey; hint: MessageKey; icon: ReactNode }> = {
  'on-device': { text: 'voice.mode.onDevice', hint: 'voice.mode.onDeviceHint', icon: <Cpu size={14} aria-hidden="true" /> },
  cloud: { text: 'voice.mode.cloud', hint: 'voice.mode.cloudHint', icon: <Globe2 size={14} aria-hidden="true" /> },
  typing: { text: 'voice.mode.typing', hint: 'voice.mode.typingHint', icon: <Keyboard size={14} aria-hidden="true" /> },
};
const modeLabel = (mode: RecognitionMode) => ({ text: t(modeKeys[mode].text), hint: t(modeKeys[mode].hint), icon: modeKeys[mode].icon });

function stored(key: string, fallback: string): string {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function store(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* preferences are optional */ }
}

export function VoiceView({ onOpenApprovals, onOpenDeveloper }: { onOpenApprovals?: () => void; onOpenDeveloper?: () => void } = {}) {
  const [session, setSession] = useState<VoiceSession | null>(null);
  // Without a saved speech language, an Arabic interface listens and speaks in Arabic.
  const [locale, setLocale] = useState(() => stored('anum.voice.locale', getLocale() === 'ar' ? 'ar-SA' : navigator.language || 'en-US'));
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
  const [pendingApprovals, setPendingApprovals] = useState(0);
  // The orb full screen (docs/voice.md#the-space-orb): opened by the orb or the Full screen button.
  const [fullscreen, setFullscreen] = useState(false);
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
  // The microphone's analyser while listening; the orb reads it once per frame (no loop of its own).
  const meter = useRef<{ stream: MediaStream; context: AudioContext; analyser: AnalyserNode; data: Uint8Array<ArrayBuffer> } | null>(null);
  const meterGeneration = useRef(0);
  const logEnd = useRef<HTMLLIElement | null>(null);
  const askRef = useRef<(text?: string) => Promise<void>>(async () => undefined);
  // The streamed reply in flight; aborting it stops the model and the spoken queue.
  const replyAbort = useRef<AbortController | null>(null);
  const startRef = useRef<(holdToTalk?: boolean, followUpTurn?: boolean) => Promise<void>>(async () => undefined);

  const wakeOnRef = useRef(wakeOn);
  wakeOnRef.current = wakeOn;
  // The example phrases are meant to be said, so they follow the speech language.
  const speech = locale.toLowerCase().startsWith('ar') ? 'ar' : 'en';
  const idleHint = wakeOn ? t('voice.idleWake', { name }) : t('voice.idleTap');
  const stateText: Record<ConsoleState, string> = {
    idle: wakeOn ? t('voice.state.listeningFor', { name }) : t('voice.state.ready'),
    listening: t('voice.state.listening'),
    thinking: t('voice.state.thinking'),
    speaking: t('voice.state.speaking', { name }),
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
    stopMeter();
    const generation = meterGeneration.current;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // Listening ended (or restarted) while the browser was asking: release this microphone.
      if (generation !== meterGeneration.current) { stream.getTracks().forEach((track) => track.stop()); return; }
      const context = new AudioContext();
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      context.createMediaStreamSource(stream).connect(analyser);
      meter.current = { stream, context, analyser, data: new Uint8Array(analyser.frequencyBinCount) };
    } catch {
      // The level meter is decorative; recognition still works without it.
    }
  }

  function stopMeter() {
    meterGeneration.current += 1;
    if (!meter.current) return;
    meter.current.stream.getTracks().forEach((track) => track.stop());
    void meter.current.context.close();
    meter.current = null;
  }

  /** The level the orb shows: the microphone while listening, the reply audio while speaking. */
  const orbLevel = useCallback(() => {
    if (meter.current) {
      const { analyser, data } = meter.current;
      analyser.getByteFrequencyData(data);
      let sum = 0;
      for (const value of data) sum += value;
      return Math.min(1, sum / data.length / 90);
    }
    return outputAmplitude();
  }, []);

  async function startListening(holdToTalk = false, followUpTurn = false) {
    if (state === 'listening' || state === 'thinking') return;
    followUp.current = followUpTurn;
    stopSpeaking();
    const activeMode = await ensureMode();
    if (activeMode === 'typing') { setNotice(t('voice.mode.typingHint')); return; }
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
          if (!followUp.current) setNotice(t('voice.notHeard'));
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
            onFallback: () => setNotice(t('voice.naturalFallback')),
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
      setNotice(isBudgetExceeded(error) ? t('common.budgetReached', { message: budgetMessage(error) }) : error instanceof Error ? error.message : t('voice.noAnswer'));
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
      if (activeMode === 'typing') { setNotice(t('voice.mode.typingHint')); return; }
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
      setNotice(t('voice.taskCreated', { title: created.task.title }));
    } catch (error) {
      setNotice(isBudgetExceeded(error) ? t('common.budgetReached', { message: budgetMessage(error) }) : error instanceof Error ? error.message : t('voice.taskFailed'));
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
    setNotice(t('voice.cleared'));
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
      setNotice(t('voice.cloudWake', { name }));
    } else if (!next) {
      setNotice('');
    }
  }

  // The page orb opens the full screen and starts talking there; inside it the orb only talks.
  function onStageOrbClick() {
    setFullscreen(true);
    onOrbClick();
  }

  function onOrbClick() {
    if (state === 'listening') stopListening();
    else if (state === 'speaking') { replyAbort.current?.abort(); stopSpeaking(); setState('idle'); wake.current?.resume(); }
    else void startListening();
  }

  function onDraftKey(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void ask(); }
  }
  const spaceMode = voiceSpaceMode(state, pendingApprovals > 0);
  const modeInfo = modeLabel(mode);
  const caption = notice || (state === 'listening' ? (draft || t('voice.goAhead')) : state === 'idle' && !needsTap ? idleHint : '');

  return <section className="voiceConsole" data-state={state} aria-label={t('voice.label', { name }, { isolate: false })}>
    <header className="voiceTopline">
      <span className="voiceBadge" title={modeInfo.hint}>{modeInfo.icon}{modeInfo.text}</span>
      <div className="voiceConsoleMeta">
        <button type="button" className="voiceIconButton" onClick={() => setFullscreen(true)} aria-label={t('voice.fullscreen.open')} title={t('voice.fullscreen.open')}>
          <Maximize2 size={18} aria-hidden="true" />
        </button>
        <button type="button" className={wakeOn ? 'voicePill on' : 'voicePill'} onClick={() => void toggleWake()} aria-pressed={wakeOn} title={t('voice.wakeTitle', { name }, { isolate: false })}>
          <Radio size={16} aria-hidden="true" />{wakeOn ? t('voice.wakeOn', { name }) : t('voice.wakeOff', { name })}
        </button>
        <button type="button" className="voiceIconButton" onClick={() => { setMuted(!muted); if (!muted) stopSpeaking(); }} aria-pressed={muted} aria-label={muted ? t('voice.unmute') : t('voice.mute')} title={muted ? t('voice.mutedTitle') : t('voice.unmutedTitle')}>
          {muted ? <VolumeX size={18} aria-hidden="true" /> : <Volume2 size={18} aria-hidden="true" />}
        </button>
      </div>
    </header>

    <div className="voiceStage">
      <button
        type="button"
        className="voiceOrb voiceTalk"
        style={{ width: orbSize, height: orbSize }}
        onClick={onStageOrbClick}
        disabled={state === 'thinking'}
        aria-label={state === 'listening' ? t('voice.stopListening') : state === 'speaking' ? t('voice.stopSpeaking') : t('voice.tapToTalk')}
      >
        <span className="voiceOrbHalo" aria-hidden="true" />
        <span className="voiceOrbGlow" aria-hidden="true" />
        {/* One live space scene at a time: the page orb rests while the full screen is open. */}
        {fullscreen ? <><span className="voiceOrbBlob b1" /><span className="voiceOrbBlob b2" /><span className="voiceOrbBlob b3" /></>
          : <SpaceOrb variant="compact" mode={spaceMode} getLevel={orbLevel} fallback={<><span className="voiceOrbBlob b1" /><span className="voiceOrbBlob b2" /><span className="voiceOrbBlob b3" /></>} />}
        <span className="voiceOrbIcon" aria-hidden="true">{state === 'listening' ? <Square size={26} /> : state === 'speaking' ? <VolumeX size={28} /> : <Mic size={30} />}</span>
      </button>
      <h2 className="voiceGreeting" dir="auto">{greeting(name, locale, hour)}</h2>
      <p className="voiceStateLabel" role="status" aria-live="polite">{stateText[state]}</p>
      <p className={state === 'listening' && draft ? 'voiceCaption live' : 'voiceCaption'} dir="auto">{caption}</p>
      <div className="voiceStageActions">
        {needsTap && !micBlocked && <button type="button" className="voiceAllowMic" onClick={() => setArmed(true)}><Radio size={16} aria-hidden="true" />{t('voice.startListening', { name })}</button>}
        {micBlocked && <button type="button" className="voiceAllowMic" onClick={() => { setMicBlocked(false); setNotice(''); setArmed(true); setWakeOn(false); window.setTimeout(() => setWakeOn(true), 0); }}>{t('voice.allowMic')}</button>}
        {pendingApprovals > 0 && <button type="button" className="voiceScope" onClick={onOpenApprovals}>
          {t('voice.approvalsWaiting', { count: pendingApprovals })}
        </button>}
      </div>
    </div>

    {developer.panel}
    {fullscreen && <OrbFullscreen name={name} mode={spaceMode} getLevel={orbLevel} stateLabel={stateText[state]}
      heard={state === 'listening' ? (draft || t('voice.goAhead')) : [...turns].reverse().find((turn) => turn.speaker === 'you' && turn.text)?.text ?? ''}
      answer={state === 'listening' ? '' : [...turns].reverse().find((turn) => turn.speaker === 'assistant' && turn.text)?.text ?? notice}
      talkLabel={state === 'listening' ? t('voice.stopListening') : state === 'speaking' ? t('voice.stopSpeaking') : t('voice.tapToTalk')}
      talkDisabled={state === 'thinking'} onTalk={onOrbClick} onClose={() => { if (state === 'listening') stopListening(); setFullscreen(false); }}
      fallback={<><span className="voiceOrbBlob b1" /><span className="voiceOrbBlob b2" /><span className="voiceOrbBlob b3" /></>} />}

    <ol className="voiceLog" aria-label={t('voice.conversation')}>
      {turns.length === 0 && <li className="voiceEmpty">
        <span className="voiceEmptyLead">{t('voice.trySaying')}</span>
        {(['voice.example.name', 'voice.example.status', 'voice.example.task'] as const).map((key) => <span key={key} lang={speech} dir="auto">“{translate(speech, key)}”</span>)}
      </li>}
      {turns.filter((turn) => turn.text).map((turn) => <li key={turn.id} className={`voiceTurn ${turn.speaker}`}>
        {turn.speaker === 'assistant' && <span className="voiceSpeaker" dir="auto">{name}</span>}
        <p dir="auto">{turn.text}</p>
        {turn.speaker === 'assistant' && turn.result && <CopyAction text={turn.text} label={t('message.copyAnswer')} />}
        {turn.result && turn.result.risk_tier !== 'read' && <span className={`voiceTier ${turn.result.risk_tier}`}>{t(`voice.tier.${turn.result.risk_tier}`)}</span>}
        {turn.result?.risk_tier === 'confirm' && turn.result.proposed_task && !turn.resolved && <div className="voiceConfirm">
          <button type="button" onClick={() => void confirmTask(turn)}><Check size={16} aria-hidden="true" />{t('voice.createTask')}</button>
          <button type="button" className="secondary" onClick={() => dismiss(turn)}><X size={16} aria-hidden="true" />{t('voice.notNow')}</button>
        </div>}
        {turn.resolved === 'created' && <span className="voiceResolved">{t('voice.taskCreatedBadge')}</span>}
        {turn.result?.risk_tier === 'visual_only' && onOpenApprovals && <div className="voiceConfirm">
          <button type="button" className="secondary" onClick={onOpenApprovals}><ShieldAlert size={16} aria-hidden="true" />{t('voice.openApprovals')}</button>
        </div>}
      </li>)}
      <li ref={logEnd} aria-hidden="true" className="voiceLogEnd" />
    </ol>

    <footer className="voiceDock">
      <button type="button" className="voiceDockMic voiceTalk" onClick={onOrbClick} disabled={state === 'thinking'} aria-label={state === 'listening' ? t('voice.stopListening') : t('voice.pushToTalk')} title={t('voice.pushToTalkTitle')}>
        {state === 'listening' ? <Square size={18} aria-hidden="true" /> : <Mic size={18} aria-hidden="true" />}
      </button>
      <label className="voiceDraft">
        <span className="visuallyHidden">{t('voice.yourMessage')}</span>
        <textarea dir="auto" value={state === 'listening' ? '' : draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onDraftKey} rows={1} placeholder={t('voice.messagePlaceholder', { name }, { isolate: false })} disabled={state === 'listening'} />
      </label>
      <button type="button" className="voiceAsk" onClick={() => void ask()} disabled={!draft.trim() || state === 'thinking' || state === 'listening'} aria-label={t('voice.send')} title={t('voice.send')}><ArrowUp size={18} aria-hidden="true" /></button>
    </footer>

    <details className="voiceSettings">
      <summary><Settings2 size={16} aria-hidden="true" />{t('voice.settings')}</summary>
      <div className="voiceSettingsGrid">
        <label className="field"><span>{t('voice.assistantName')}</span>
          <input dir="auto" value={nameDraft} maxLength={40} onChange={(event) => setNameDraft(event.target.value)} onBlur={saveName} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); saveName(); } }} />
        </label>
        <label className="field"><span>{t('voice.language')}</span><select value={locale} onChange={(event) => setLocale(event.target.value)} disabled={state !== 'idle'}>
          <option value="en-US">English (US)</option><option value="en-GB">English (UK)</option><option value="ar-SA">العربية (السعودية)</option><option value="ar-AE">العربية (الإمارات)</option>
          {!['en-US', 'en-GB', 'ar-SA', 'ar-AE'].includes(locale) && <option value={locale}>{locale}</option>}
        </select></label>
        <label className="field"><span>{t('voice.replyVoice')}</span><select value={voice} onChange={(event) => { setVoice(event.target.value); store('anum.voice.reply', event.target.value); }}>
          <option value="natural">{t('voice.reply.natural')}</option>
          <option value="">{t('voice.reply.device')}</option>
          {hasVoiceServer() && <option value="server">{t('voice.reply.server')}</option>}
          {rankedVoices.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
        </select></label>
        <label className="field"><span>{t('voice.retention')}</span><select value={retention} onChange={(event) => setRetention(event.target.value as TranscriptRetention)} disabled={Boolean(session)}>
          {(['session', '30_days', 'permanent'] as const).map((choice) => <option key={choice} value={choice}>{t(`voice.retention.${choice}`)}</option>)}
        </select></label>
      </div>
      {voice === 'natural' && !naturalVoiceSupports(locale) && <p className="muted">{t('voice.naturalEnglishOnly')}</p>}
      <p className="muted">{t('voice.neverByVoice', { name })}</p>
      {session && <button type="button" className="secondary" onClick={() => void endSession()}>{t('voice.clear')}</button>}
    </details>
  </section>;
}
