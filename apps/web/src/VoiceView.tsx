import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import { ArrowUp, Check, Cpu, Globe2, Keyboard, Maximize2, MessageSquareText, Mic, Radio, Settings2, ShieldAlert, Square, Volume2, VolumeX, X } from 'lucide-react';
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
  recognitionIdle,
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
import { SpaceOrb } from './SpaceOrb';
import { VoiceActionCardView } from './VoiceActionCard';
import { voiceSpaceMode } from './lib/spaceMode';
import { heardOwnReply } from './lib/recognitionGate';
import { enterFocus, exitFocus, focusElement, type FocusMode } from './lib/focusMode';
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

export function VoiceView({ onOpenApprovals, onOpenDeveloper, onOpenTask }: { onOpenApprovals?: () => void; onOpenDeveloper?: () => void; onOpenTask?: (taskId: string) => void } = {}) {
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
  // The page is the space scene (docs/voice.md#the-space-orb): the orb is drawn under the talk
  // button and the sprites stay above the text panel.
  const talkRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
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
  const scroller = useRef<HTMLDivElement | null>(null);
  const askRef = useRef<(text?: string) => Promise<void>>(async () => undefined);
  // The streamed reply in flight; aborting it stops the model and the spoken queue.
  const replyAbort = useRef<AbortController | null>(null);
  const startRef = useRef<(holdToTalk?: boolean, followUpTurn?: boolean, interrupt?: boolean) => Promise<void>>(async () => undefined);
  // Each listening turn gets a number; tapping again, leaving the tab or a newer turn makes an older
  // turn that is still starting give up.
  const listenTurn = useRef(0);
  // The probe for on-device recognition, started when the page opens so the first tap never waits.
  const modeProbe = useRef<Promise<RecognitionMode> | null>(null);
  const modeRef = useRef<RecognitionMode>(mode);
  modeRef.current = mode;
  const stateRef = useRef<ConsoleState>(state);
  stateRef.current = state;
  // What the assistant is saying, so the wake listener can tell the name said over a reply from the reply itself.
  const spokenReply = useRef('');
  // Listening stops when the tab is left; it starts again only with a tap (docs/voice.md#leaving-the-tab).
  const [away, setAway] = useState(false);
  // Focus mode (docs/voice.md#full-screen-focus): only the scene and the orb.
  const spaceRef = useRef<HTMLElement>(null);
  const [focus, setFocus] = useState<FocusMode>(false);
  const [focusHint, setFocusHint] = useState(false);
  // The conversation panel is off by default: the scene, the orb and one line of what is happening
  // (docs/voice.md#the-conversation-panel). The choice is kept in this browser.
  const [chatOn, setChatOn] = useState(() => stored('anum.voice.chat', 'off') === 'on');

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

  useEffect(() => {
    probedLocale.current = null;
    setMode(baseRecognitionMode());
    store('anum.voice.locale', locale);
    // Ask now (it needs no gesture) whether on-device recognition is ready, so the first tap does
    // not wait for the answer before the microphone starts.
    let live = true;
    const probe = detectRecognitionMode(locale);
    modeProbe.current = probe;
    void probe.then((detected) => { if (!live) return; probedLocale.current = locale; setMode(detected); });
    return () => { live = false; };
  }, [locale]);
  useEffect(() => { void listSystemVoices().then(setVoices); }, []);
  // New turns scroll the panel's own list, never the page.
  useEffect(() => { const box = scroller.current; if (turns.length && box) box.scrollTop = box.scrollHeight; }, [turns]);
  useEffect(() => () => { replyAbort.current?.abort(); stopMeter(); stopSpeaking(); controller.current?.cancel(); wake.current?.stop(); }, []);

  async function ensureMode(): Promise<RecognitionMode> {
    if (probedLocale.current === locale) return modeRef.current;
    const detected = await (modeProbe.current ?? detectRecognitionMode(locale));
    probedLocale.current = locale;
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

  async function startListening(holdToTalk = false, followUpTurn = false, interrupt = false) {
    if (!interrupt && (stateRef.current === 'listening' || stateRef.current === 'thinking')) return;
    const turn = ++listenTurn.current;
    followUp.current = followUpTurn;
    setAway(false);
    stopSpeaking();
    // Show listening at once (docs/voice.md#listening-quickly-and-reliably); the microphone follows.
    stateRef.current = 'listening';
    setState('listening');
    setNotice('');
    heard.current = '';
    setDraft('');
    const activeMode = await ensureMode();
    if (turn !== listenTurn.current) return;
    if (activeMode === 'typing') { setState('idle'); setNotice(t('voice.mode.typingHint')); return; }
    // The wake listener hands the microphone over: wait until it has really stopped.
    wake.current?.pause();
    await recognitionIdle();
    if (turn !== listenTurn.current) return;
    const current = createPushToTalk(
      (text) => { heard.current = text; setDraft(text); },
      (message) => {
        if (turn !== listenTurn.current) return;
        listenTurn.current += 1;
        setNotice(message); setState('idle'); stopMeter(); wake.current?.resume();
      },
      locale,
      () => {
        if (turn !== listenTurn.current) return;
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
    controller.current = current;
    void startMeter();
    await current.start();
  }
  startRef.current = startListening;

  const stopListening = useCallback(() => {
    if (controller.current) { controller.current.stop(); return; }
    // Still starting: give up on this turn.
    listenTurn.current += 1;
    setState('idle');
    wake.current?.resume();
  }, []);

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
    spokenReply.current = '';
    const speakQueue = () => {
      if (muted || speaking || !queue.length || abort.signal.aborted) return;
      setState('speaking');
      // Saying the name over the reply interrupts it (barge-in), so the wake listener runs again.
      if (wakeOnRef.current) wake.current?.resume();
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
      spokenReply.current += ` ${split.sentences.join(' ')}`;
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
      // Interrupted (a tap, the name, leaving the tab): whoever interrupted decides what comes next.
      if (abort.signal.aborted) return;
      setState('idle');
      wake.current?.resume();
      setNotice(isBudgetExceeded(error) ? t('common.budgetReached', { message: budgetMessage(error) }) : error instanceof Error ? error.message : t('voice.noAnswer'));
    } finally {
      if (replyAbort.current === abort) replyAbort.current = null;
    }
  }
  askRef.current = ask;

  // Wake name: listen in the background only while switched on; paused while talking or speaking.
  useEffect(() => {
    if (!wakeOn) { setNeedsTap(false); return; }
    // After leaving the tab the name is not listened for until the person is back and taps.
    if (away) { setNeedsTap(true); return; }
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
      wake.current = createWakeListener(name, locale, activeMode === 'on-device', (rest, said) => {
        const interrupting = stateRef.current === 'speaking' || stateRef.current === 'thinking';
        // The reply coming back through the speakers is not the person saying the name.
        if (stateRef.current === 'speaking' && heardOwnReply(said, spokenReply.current)) return;
        if (interrupting) { replyAbort.current?.abort(); stopSpeaking(); }
        playWakeChime();
        if (rest) void askRef.current(rest);
        else void startRef.current(false, false, interrupting);
      }, (message) => { setNotice(message); setMicBlocked(true); });
      wake.current?.start();
      setMicBlocked(false);
    });
    return () => { cancelled = true; wake.current?.stop(); wake.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wakeOn, armed, away, name, locale]);

  // Leaving the tab (hidden, another window focused, the page closing) stops everything that
  // listens or speaks and releases the microphone; coming back shows standby (docs/voice.md#leaving-the-tab).
  const suspendRef = useRef(() => undefined as void);
  suspendRef.current = () => {
    const active = stateRef.current !== 'idle' || wake.current !== null || meter.current !== null || controller.current !== null;
    listenTurn.current += 1;
    replyAbort.current?.abort();
    stopSpeaking();
    controller.current?.cancel();
    controller.current = null;
    stopMeter();
    wake.current?.stop();
    wake.current = null;
    stateRef.current = 'idle';
    setState('idle');
    if (active) { setAway(true); setNotice(t('voice.away')); }
  };
  useEffect(() => {
    const leave = () => suspendRef.current();
    const hidden = () => { if (document.visibilityState === 'hidden') leave(); };
    // Focus can move inside the page (an iframe, the address bar) and come straight back; only a
    // window that is still without focus a moment later counts as left.
    const blurred = () => window.setTimeout(() => { if (!document.hasFocus()) leave(); }, 150);
    document.addEventListener('visibilitychange', hidden);
    window.addEventListener('blur', blurred);
    window.addEventListener('pagehide', leave);
    return () => {
      document.removeEventListener('visibilitychange', hidden);
      window.removeEventListener('blur', blurred);
      window.removeEventListener('pagehide', leave);
    };
  }, []);

  // Focus mode: the browser's full screen on the scene, or a fixed overlay where elements cannot
  // go full screen (iPhone Safari). Esc, the browser's own exit, a double tap or a swipe down leave.
  async function openFocus() {
    const element = spaceRef.current;
    if (!element) return;
    setFocus(await enterFocus(element));
    setFocusHint(true);
    window.setTimeout(() => setFocusHint(false), 2400);
    window.setTimeout(() => talkRef.current?.focus(), 0);
  }
  const closeFocus = useCallback(() => { void exitFocus(); setFocus(false); setFocusHint(false); }, []);
  useEffect(() => {
    if (!focus) return;
    const changed = () => { if (focus === 'native' && !focusElement()) closeFocus(); };
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); closeFocus(); } };
    document.addEventListener('fullscreenchange', changed);
    document.addEventListener('webkitfullscreenchange', changed);
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('fullscreenchange', changed);
      document.removeEventListener('webkitfullscreenchange', changed);
      document.removeEventListener('keydown', key);
    };
  }, [focus, closeFocus]);
  // Leaving the page while in focus mode leaves the browser's full screen too.
  useEffect(() => () => { void exitFocus(); }, []);
  const lastTap = useRef(0);
  const swipe = useRef<{ x: number; y: number } | null>(null);
  const onFocusPointerDown = (event: React.PointerEvent) => {
    if (!focus || (event.target as HTMLElement).closest('.voiceSpaceTalk')) return;
    swipe.current = { x: event.clientX, y: event.clientY };
  };
  const onFocusPointerUp = (event: React.PointerEvent) => {
    if (!focus || !swipe.current) return;
    const dx = event.clientX - swipe.current.x, dy = event.clientY - swipe.current.y;
    swipe.current = null;
    if (dy > 90 && Math.abs(dx) < 70) { closeFocus(); return; }
    const now = Date.now();
    if (now - lastTap.current < 320) { lastTap.current = 0; closeFocus(); } else lastTap.current = now;
  };

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

    function onOrbClick() {
    const current = stateRef.current;
    if (current === 'listening') stopListening();
    // Barge-in: a tap while the assistant thinks or speaks stops the reply and listens at once.
    else if (current === 'speaking' || current === 'thinking') { replyAbort.current?.abort(); stopSpeaking(); void startListening(false, false, true); }
    else void startListening();
  }

  function onDraftKey(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void ask(); }
  }
  const spaceMode = voiceSpaceMode(state, pendingApprovals > 0);
  const modeInfo = modeLabel(mode);
  const caption = notice || (state === 'listening' ? (draft || t('voice.goAhead')) : state === 'idle' && !needsTap ? idleHint : '');
  const lastAnswer = [...turns].reverse().find((turn) => turn.speaker === 'assistant' && turn.text)?.text ?? '';
  // Focus mode shows no controls, only a faint line of what was heard or answered.
  const focusCaption = state === 'listening' ? draft : state === 'speaking' || state === 'thinking' ? lastAnswer : '';

  const stageActions = <div className="voiceStageActions">
    {needsTap && !micBlocked && <button type="button" className="voiceAllowMic" onClick={() => { setArmed(true); setAway(false); setNotice(''); }}><Radio size={16} aria-hidden="true" />{t('voice.startListening', { name })}</button>}
    {micBlocked && <button type="button" className="voiceAllowMic" onClick={() => { setMicBlocked(false); setNotice(''); setArmed(true); setWakeOn(false); window.setTimeout(() => setWakeOn(true), 0); }}>{t('voice.allowMic')}</button>}
    {pendingApprovals > 0 && <button type="button" className="voiceScope" onClick={onOpenApprovals}>
      {t('voice.approvalsWaiting', { count: pendingApprovals })}
    </button>}
  </div>;
  // What waits for the person while the panel is hidden: the latest turn that needs a tap (create a
  // proposed task, or open Approvals for something voice may not do). Approvals themselves are
  // never decided by voice or from here: they open in Approvals with the payload on screen.
  const decision = [...turns].reverse().find((turn) => turn.speaker === 'assistant' && turn.result && !turn.resolved
    && ((turn.result.risk_tier === 'confirm' && turn.result.proposed_task) || turn.result.risk_tier === 'visual_only'));
  const briefText = notice || (state === 'listening' ? draft || t('voice.goAhead') : state === 'speaking' || state === 'thinking' ? lastAnswer : state === 'idle' && !needsTap ? idleHint : '');
  function toggleChat() {
    const next = !chatOn;
    setChatOn(next);
    store('anum.voice.chat', next ? 'on' : 'off');
  }

  const blobs = <><span className="voiceOrbBlob b1" /><span className="voiceOrbBlob b2" /><span className="voiceOrbBlob b3" /></>;

  return <section ref={spaceRef} className={`voiceSpace${focus ? ` focus${focus === 'overlay' ? ' overlay' : ''}` : ''}`} data-state={state} data-focus={focus || undefined} aria-labelledby="view-title"
    onPointerDown={onFocusPointerDown} onPointerUp={onFocusPointerUp}>
    <h1 id="view-title" className="visuallyHidden">{t('voice.label', { name }, { isolate: false })}</h1>
    <SpaceOrb variant="full" className="voiceSpaceStage" mode={spaceMode} getLevel={orbLevel} orbAnchor={talkRef} floorAnchor={panelRef} fallback={null}>{(controls) => <div className="voiceSpaceLayout">
    <header className="voiceSpaceBar">
      <span className="voiceBadge" title={modeInfo.hint}>{modeInfo.icon}<span className="voiceBadgeText">{modeInfo.text}</span></span>
      <div className="voiceConsoleMeta">
        <button type="button" className={wakeOn ? 'voicePill on' : 'voicePill'} onClick={() => void toggleWake()} aria-pressed={wakeOn} title={t('voice.wakeTitle', { name }, { isolate: false })}>
          <Radio size={16} aria-hidden="true" /><span className="voicePillText">{wakeOn ? t('voice.wakeOn', { name }) : t('voice.wakeOff', { name })}</span>
        </button>
        <button type="button" className="voiceIconButton" onClick={() => { setMuted(!muted); if (!muted) stopSpeaking(); }} aria-pressed={muted} aria-label={muted ? t('voice.unmute') : t('voice.mute')} title={muted ? t('voice.mutedTitle') : t('voice.unmutedTitle')}>
          {muted ? <VolumeX size={18} aria-hidden="true" /> : <Volume2 size={18} aria-hidden="true" />}
        </button>
        {controls}
        <button type="button" className="voiceIconButton" onClick={toggleChat} aria-pressed={chatOn} aria-label={t('voice.chat.toggle')} title={chatOn ? t('voice.chat.hide') : t('voice.chat.show')}>
          <MessageSquareText size={18} aria-hidden="true" />
        </button>
        <button type="button" className="voiceIconButton" onClick={() => void openFocus()} aria-label={t('voice.focus.open')} title={t('voice.focus.open')}>
          <Maximize2 size={18} aria-hidden="true" />
        </button>
      </div>
      <details className="voiceSettings voiceSpaceSettings" onKeyDown={(event) => { if (event.key === 'Escape') { event.currentTarget.open = false; event.currentTarget.querySelector('summary')?.focus(); } }}>
        <summary className="voiceIconButton" aria-label={t('voice.settings')} title={t('voice.settings')}><Settings2 size={18} aria-hidden="true" /></summary>
        <div className="voiceSpaceSettingsCard">
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
        </div>
      </details>
    </header>

    <div className="voiceSpaceCentre">
      {/* The orb is the talk button: the scene draws the orb under it, at its size. */}
      <button
        ref={talkRef}
        type="button"
        className="voiceOrb voiceTalk voiceSpaceTalk"
        onClick={onOrbClick}
        aria-label={state === 'listening' ? t('voice.stopListening') : state === 'speaking' ? t('voice.stopSpeaking') : t('voice.tapToTalk')}
      >
        <span className="voiceSpaceFallback" aria-hidden="true">{blobs}</span>
        <span className="voiceOrbIcon" aria-hidden="true">{state === 'listening' ? <Square size={30} /> : state === 'speaking' ? <VolumeX size={32} /> : <Mic size={34} />}</span>
      </button>
    </div>

    {focus && <div className="voiceFocusText">
      <p className="voiceFocusCaption" role="status" aria-live="polite" dir="auto">
        <span className="visuallyHidden">{stateText[state]} </span>{focusCaption}
      </p>
      {focusHint && <p className="voiceFocusHint">{t(matchMedia('(hover: none)').matches ? 'voice.focus.hintTouch' : 'voice.focus.hintKeys')}</p>}
    </div>}

    <div className="voiceSpaceBottom" ref={panelRef}>
    {!chatOn && <div className="voiceSpaceBrief">
      <p className="voiceBriefLine" role="status" aria-live="polite" dir="auto">
        <span className="voiceBriefState">{stateText[state]}</span>{briefText && <span className="voiceBriefText">{briefText}</span>}
      </p>
      {stageActions}
      {decision?.result && <div className="voiceDecision" role="group" aria-label={t('voice.decision.label')}>
        <p dir="auto">{decision.result.risk_tier === 'confirm' && decision.result.proposed_task ? t('voice.decision.task', { title: decision.result.proposed_task }) : decision.text}</p>
        <div className="voiceConfirm">
          {decision.result.risk_tier === 'confirm' ? <>
            <button type="button" onClick={() => void confirmTask(decision)}><Check size={16} aria-hidden="true" />{t('voice.createTask')}</button>
            <button type="button" className="secondary" onClick={() => dismiss(decision)}><X size={16} aria-hidden="true" />{t('voice.notNow')}</button>
          </> : <>
            {onOpenApprovals && <button type="button" onClick={onOpenApprovals}><ShieldAlert size={16} aria-hidden="true" />{t('voice.openApprovals')}</button>}
            <button type="button" className="secondary" onClick={() => dismiss(decision)}><X size={16} aria-hidden="true" />{t('voice.notNow')}</button>
          </>}
        </div>
      </div>}
    </div>}
    {chatOn && <div className="voiceSpacePanel">
      <div className="voiceSpaceStatus">
        <p className="voiceStateLabel" role="status" aria-live="polite">{stateText[state]}</p>
        {turns.length === 0 && state === 'idle' && <h2 className="voiceGreeting" dir="auto">{greeting(name, locale, hour)}</h2>}
        <p className={state === 'listening' && draft ? 'voiceCaption live' : 'voiceCaption'} dir="auto">{caption}</p>
      </div>
      {/* Status above, the dock below; the approvals notice, the conversation and the settings scroll between them. */}
      <div className="voiceSpaceScroll" ref={scroller}>
      {stageActions}

      {developer.panel}
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
        {turn.result?.action && <VoiceActionCardView card={turn.result.action} assistantName={name} onOpenTask={onOpenTask} onOpenApprovals={onOpenApprovals} />}
      </li>)}
    </ol>

      </div>
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
    </div>}
    </div>
    </div>}</SpaceOrb>
  </section>;
}
