// Voice / video call with the assistant (docs/voice.md#call-mode): a lobby to pick the language
// and speech engines, the call screen (timer, status, orb, Mute, Video, Speaker, Chat, More,
// End), the transcript panel and the end-of-call summary. The loop is lib/callEngine.ts.
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { ArrowUp, Camera, CameraOff, Check, Download, Ellipsis, MessageSquareText, Mic, MicOff, Phone, PhoneOff, Send, ShieldAlert, SwitchCamera, Volume2, VolumeX, X } from 'lucide-react';
import { Orb3D, type OrbState } from './Orb3D';
import { formatNumber, t, type MessageKey } from './lib/i18n';
import { budgetFallbackMessage } from './lib/errors';
import { callLocaleFor, DEVICE_ONLY, engineCost, formatCallTime, isArabicLocale, latencyVerdict, serverEngine, type VoiceCapabilities } from './lib/call';
import { CallEngine, type CallProblem, type CallSnapshot } from './lib/callEngine';
import { callApi, getVoiceCapabilities } from './lib/callApi';
import { captureFrame, createCallRecognizer, createCallSpeaker, openCamera, openMicrophone, type Facing, type ReplyEngine } from './lib/callBrowser';
import { detectRecognitionMode, naturalVoiceSupports, type TranscriptRetention } from './lib/voice';
import './voice.css';
import './call.css';

function stored(key: string, fallback: string): string {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function store(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* preferences are optional */ }
}

const LOCALE_LABELS: Record<string, string> = { 'en-US': 'English (US)', 'en-GB': 'English (UK)', 'ar-SA': 'العربية (السعودية)', 'ar-AE': 'العربية (الإمارات)' };

type Stage = 'lobby' | 'call' | 'ending' | 'ended';

function orbState(snapshot: CallSnapshot | null): OrbState {
  switch (snapshot?.phase) {
    case 'listening': case 'hearing': return 'listening';
    case 'thinking': return 'thinking';
    case 'speaking': return 'speaking';
    default: return 'idle';
  }
}

function problemText(problem: CallProblem, capabilities: VoiceCapabilities): string {
  switch (problem.kind) {
    case 'budget': return problem.detail ?? budgetFallbackMessage();
    case 'provider': return problem.detail ? t('call.problem.providerDetail', { detail: problem.detail }) : t('call.problem.provider');
    case 'vision-off': return capabilities.vision.reason ?? t('call.problem.vision-off');
    case 'summary': return problem.detail ? t('call.problem.summaryDetail', { detail: problem.detail }) : t('call.problem.summary');
    case 'session': return t('call.problem.session');
    default: return t(`call.problem.${problem.kind}` as MessageKey);
  }
}

export function CallView({ onOpenApprovals, onOpenConversation }: { onOpenApprovals?: () => void; onOpenConversation?: (id: string) => void } = {}) {
  const [stage, setStage] = useState<Stage>('lobby');
  const [capabilities, setCapabilities] = useState<VoiceCapabilities | null>(null);
  const [locale, setLocale] = useState(() => callLocaleFor(document.documentElement.lang || 'en', stored('anum.call.locale', '')));
  const [stt, setStt] = useState<'device' | 'server'>(() => stored('anum.call.stt', 'device') === 'server' ? 'server' : 'device');
  const [tts, setTts] = useState<ReplyEngine>(() => (['device', 'natural', 'server'] as const).find((value) => value === stored('anum.call.tts', 'device')) ?? 'device');
  const [retention, setRetention] = useState<TranscriptRetention>('session');
  const [name] = useState(() => stored('anum.voice.name', 'Anum'));
  const [snapshot, setSnapshot] = useState<CallSnapshot | null>(null);
  const [lobbyProblem, setLobbyProblem] = useState<CallProblem | null>(null);
  const [starting, setStarting] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [chatOpen, setChatOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [camera, setCamera] = useState<MediaStream | null>(null);
  const [facing, setFacing] = useState<Facing>('environment');
  const [lastFrame, setLastFrame] = useState<string | null>(null);
  const [cameraProblem, setCameraProblem] = useState<CallProblem | null>(null);
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const engine = useRef<CallEngine | null>(null);
  const video = useRef<HTMLVideoElement | null>(null);
  const sessionRef = useRef<string | null>(null);
  const dialog = useRef<HTMLDivElement | null>(null);
  const logEnd = useRef<HTMLLIElement | null>(null);
  const caps = capabilities ?? DEVICE_ONLY;
  const arabic = isArabicLocale(locale);
  const serverStt = serverEngine(caps.stt);
  const serverTts = serverEngine(caps.tts);

  useEffect(() => { void getVoiceCapabilities().then(setCapabilities); }, []);
  useEffect(() => { if (tts === 'natural' && !naturalVoiceSupports(locale)) setTts('device'); }, [locale, tts]);
  useEffect(() => {
    if (stage !== 'call') return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [stage]);
  useEffect(() => {
    const online = () => engine.current?.setOnline(true);
    const offline = () => engine.current?.setOnline(false);
    window.addEventListener('online', online);
    window.addEventListener('offline', offline);
    return () => { window.removeEventListener('online', online); window.removeEventListener('offline', offline); };
  }, []);
  // Leaving the page hangs up: the microphone and camera never stay on behind another view.
  useEffect(() => () => { engine.current?.hangUp(); void engine.current?.finish(false); }, []);
  useEffect(() => { if (video.current) video.current.srcObject = camera; }, [camera]);
  useEffect(() => () => camera?.getTracks().forEach((track) => track.stop()), [camera]);
  useEffect(() => { if (chatOpen) logEnd.current?.scrollIntoView({ block: 'nearest' }); }, [chatOpen, snapshot?.turns.length]);
  useEffect(() => { if (stage === 'ending' || stage === 'ended') dialog.current?.focus(); }, [stage]);

  const closeCamera = useCallback(() => {
    setCamera((current) => { current?.getTracks().forEach((track) => track.stop()); return null; });
    setLastFrame(null);
    engine.current?.setCamera(null);
  }, []);

  async function start() {
    if (starting) return;
    setStarting(true);
    setLobbyProblem(null);
    setNotice('');
    store('anum.call.locale', locale);
    store('anum.call.stt', stt);
    store('anum.call.tts', tts);
    const mode = await detectRecognitionMode(locale).catch(() => 'cloud' as const);
    // The server voice needs the session id, which is known once the call's session exists.
    const api = { ...callApi, createSession: async (...args: Parameters<typeof callApi.createSession>) => {
      const created = await callApi.createSession(...args);
      sessionRef.current = created.id;
      return created;
    } };
    const instance = new CallEngine({
      api,
      openMic: openMicrophone,
      createRecognizer: (lang, onText, onBlocked) => createCallRecognizer(lang, mode === 'on-device', onText, onBlocked),
      speaker: createCallSpeaker(() => ({ engine: tts, locale, sessionId: sessionRef.current, voice: '' }), () => setNotice(t('call.notice.voiceFallback'))),
      now: () => Date.now(),
      every: (ms, fn) => { const id = window.setInterval(fn, ms); return () => window.clearInterval(id); },
      after: (ms, fn) => { const id = window.setTimeout(fn, ms); return () => window.clearTimeout(id); },
      isOnline: () => navigator.onLine !== false,
    }, {
      locale, name, retention, stt: stt === 'server' && serverStt ? 'server' : 'device', speakerOn: true,
      lookPrompt: arabic ? 'ماذا ترى؟' : 'What do you see?', capabilities: caps,
    });
    instance.subscribe(setSnapshot);
    engine.current = instance;
    await instance.start();
    setStarting(false);
    if (instance.snapshot.phase === 'idle') {
      setLobbyProblem(instance.snapshot.problem);
      engine.current = null;
      return;
    }
    setNow(Date.now());
    setStage('call');
    if (instance.snapshot.stt === 'typing') setChatOpen(true);
  }

  async function toggleCamera() {
    const current = engine.current;
    if (!current) return;
    if (camera) { closeCamera(); return; }
    if (!caps.vision.available) { setCameraProblem({ kind: 'vision-off' }); return; }
    setCameraProblem(null);
    try {
      const stream = await openCamera(facing);
      setCamera(stream);
      current.setCamera(async () => {
        const frame = video.current ? captureFrame(video.current, caps.vision.max_frame_bytes) : null;
        if (frame) setLastFrame(`data:${frame.mediaType};base64,${frame.base64}`);
        return frame;
      });
    } catch (error) {
      const failure = (error as { name?: string } | null)?.name;
      setCameraProblem({ kind: failure === 'NotFoundError' || failure === 'OverconstrainedError' ? 'camera-missing' : 'camera-denied' });
    }
  }

  async function flipCamera() {
    if (!camera) return;
    const next: Facing = facing === 'user' ? 'environment' : 'user';
    setFacing(next);
    try {
      const stream = await openCamera(next);
      camera.getTracks().forEach((track) => track.stop());
      setCamera(stream);
    } catch {
      setCameraProblem({ kind: 'camera-missing' });
    }
  }

  function hangUp() {
    const current = engine.current;
    if (!current) return;
    closeCamera();
    current.hangUp();
    setMoreOpen(false);
    if (!current.hasConversation) { void current.finish(false).then(() => setStage('ended')); return; }
    setStage('ending');
  }

  async function finish(save: boolean) {
    const current = engine.current;
    if (!current) return;
    setSaving(true);
    const done = await current.finish(save);
    setSaving(false);
    if (done) setStage('ended');
  }

  function newCall() {
    engine.current = null;
    sessionRef.current = null;
    setSnapshot(null);
    setChatOpen(false);
    setNotice('');
    setStage('lobby');
  }

  function sendTyped(event?: FormEvent) {
    event?.preventDefault();
    const text = draft.trim();
    if (!text || !engine.current) return;
    setDraft('');
    void engine.current.submit(text);
  }

  function onDraftKey(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendTyped(); }
  }

  async function confirmTask(turnId: string) {
    const title = await engine.current?.confirmTask(turnId);
    if (title) setNotice(t('call.notice.taskCreated', { title }));
  }

  const onKey = useCallback((event: KeyboardEvent) => {
    if (event.key !== 'Escape') return;
    if (moreOpen) setMoreOpen(false);
    else if (chatOpen) setChatOpen(false);
  }, [moreOpen, chatOpen]);
  useEffect(() => { window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey); }, [onKey]);

  const elapsed = snapshot?.startedAt ? Math.max(0, Math.floor(((snapshot.endedAt ?? now) - snapshot.startedAt) / 1000)) : 0;
  const statusKey: MessageKey = !snapshot ? 'call.status.connecting'
    : snapshot.muted ? 'call.status.muted'
    : (`call.status.${snapshot.phase}` as MessageKey);
  const lastAssistant = useMemo(() => [...(snapshot?.turns ?? [])].reverse().find((turn) => turn.speaker === 'assistant' && turn.text), [snapshot?.turns]);
  const problem = cameraProblem ?? snapshot?.problem ?? null;
  const latency = snapshot?.lastLatencyMs ?? null;

  if (stage === 'lobby') {
    return <section className="callLobby surface" aria-labelledby="call-lobby-title">
      <div className="callLobbyHead">
        <span className="callLobbyIcon" aria-hidden="true"><Phone /></span>
        <div>
          <h2 id="call-lobby-title">{t('call.lobby.title', { name })}</h2>
          <p className="muted">{t('call.lobby.text', { name })}</p>
        </div>
      </div>
      {!capabilities && <p className="notice" role="status">{t('call.lobby.checking')}</p>}
      <div className="callLobbyGrid">
        <label className="field"><span>{t('call.settings.language')}</span>
          <select value={locale} onChange={(event) => setLocale(event.target.value)}>
            {caps.locales.map((value) => <option key={value} value={value} lang={value}>{LOCALE_LABELS[value] ?? value}</option>)}
          </select>
        </label>
        <label className="field"><span>{t('call.settings.recognition')}</span>
          <select value={stt} onChange={(event) => setStt(event.target.value === 'server' ? 'server' : 'device')}>
            <option value="device">{t('call.engine.deviceStt')}</option>
            {serverStt && <option value="server">{t('call.engine.server', { label: serverStt.label, model: serverStt.model ?? '' })}</option>}
          </select>
        </label>
        <label className="field"><span>{t('call.settings.voice')}</span>
          <select value={tts} onChange={(event) => setTts(event.target.value as ReplyEngine)}>
            <option value="device">{t('call.engine.deviceTts')}</option>
            {naturalVoiceSupports(locale) && <option value="natural">{t('call.engine.natural')}</option>}
            {serverTts && <option value="server">{t('call.engine.server', { label: serverTts.label, model: serverTts.model ?? '' })}</option>}
          </select>
        </label>
        <label className="field"><span>{t('call.settings.retention')}</span>
          <select value={retention} onChange={(event) => setRetention(event.target.value as TranscriptRetention)}>
            <option value="session">{t('call.retention.session')}</option>
            <option value="30_days">{t('call.retention.30_days')}</option>
            <option value="permanent">{t('call.retention.permanent')}</option>
          </select>
        </label>
      </div>
      <ul className="callFacts">
        <li>{stt === 'server' && serverStt ? costLine(serverStt, 'stt') : t('call.fact.deviceStt')}</li>
        <li>{tts === 'server' && serverTts ? costLine(serverTts, 'tts') : t('call.fact.deviceTts')}</li>
        <li>{caps.vision.available ? t('call.fact.vision', { model: caps.vision.model ?? '' }) : (caps.vision.reason ?? t('call.problem.vision-off'))}</li>
        <li>{t('call.fact.approvals')}</li>
        <li>{t('call.fact.latency', { seconds: formatNumber(caps.latency_target_ms.first_audio / 1000) })}</li>
      </ul>
      {lobbyProblem && <p className="notice errorNotice" role="alert">{problemText(lobbyProblem, caps)}</p>}
      <div className="actions">
        <button type="button" className="callStart" onClick={() => void start()} disabled={starting}>
          <Phone aria-hidden="true" />{starting ? t('call.lobby.starting') : lobbyProblem ? t('call.lobby.retry') : t('call.lobby.start')}
        </button>
      </div>
    </section>;
  }

  if (stage === 'ending' || stage === 'ended') {
    const summary = snapshot?.summary;
    return <section className="callEnd surface" aria-labelledby="call-end-title">
      <div ref={dialog} tabIndex={-1} className="callEndBody" role={stage === 'ending' ? 'dialog' : undefined} aria-modal={stage === 'ending' ? true : undefined} aria-labelledby="call-end-title">
        <p className="callEndTime">{t('call.end.duration', { time: formatCallTime(elapsed) })}</p>
        <h2 id="call-end-title">{stage === 'ending' ? t('call.end.askTitle') : t('call.end.doneTitle')}</h2>
        {stage === 'ending' && <>
          <p className="muted">{t('call.end.askText')}</p>
          {snapshot?.problem?.kind === 'summary' && <p className="notice errorNotice" role="alert">{problemText(snapshot.problem, caps)}</p>}
          <div className="actions">
            <button type="button" onClick={() => void finish(true)} disabled={saving}><Check aria-hidden="true" />{saving ? t('call.end.saving') : snapshot?.problem?.kind === 'summary' ? t('call.end.retrySave') : t('call.end.save')}</button>
            <button type="button" className="secondary" onClick={() => void finish(false)} disabled={saving}><X aria-hidden="true" />{t('call.end.discard')}</button>
          </div>
        </>}
        {stage === 'ended' && <>
          {summary ? <>
            <p className="muted">{summary.source === 'model' ? t('call.end.summaryByModel') : t('call.end.summaryFromTranscript')}</p>
            <p className="callSummaryText">{summary.summary}</p>
          </> : <p className="muted">{t('call.end.nothingKept')}</p>}
          <div className="actions">
            {summary?.conversation_id && onOpenConversation && <button type="button" onClick={() => onOpenConversation(summary.conversation_id!)}><MessageSquareText aria-hidden="true" />{t('call.end.open')}</button>}
            <button type="button" className={summary?.conversation_id ? 'secondary' : undefined} onClick={newCall}><Phone aria-hidden="true" />{t('call.end.again')}</button>
          </div>
        </>}
      </div>
    </section>;
  }

  const phase = snapshot?.phase ?? 'connecting';
  const orbSize = camera ? 120 : Math.round(Math.max(150, Math.min(220, window.innerHeight * 0.26)));
  const pending = snapshot?.pendingApprovals ?? 0;
  return <section className={`callScreen ${chatOpen ? 'withChat' : ''}`} aria-label={t('call.screen', { name })} data-phase={phase}>
    <header className="callTop">
      <div className="callWho">
        <h2>{name}</h2>
        <p className="callStatus"><span role="status" aria-live="polite">{t(statusKey)}</span><span aria-hidden="true"> · </span><time aria-label={t('call.timerLabel')} dateTime={`PT${elapsed}S`}>{formatCallTime(elapsed)}</time></p>
      </div>
      <div className="callBadges">
        {snapshot?.cameraOn && <span className="callLiveBadge" role="note"><span aria-hidden="true" className="callLiveDot" />{t('call.camera.on')}</span>}
        <span className="callBadge">{snapshot?.stt === 'server' ? t('call.badge.serverStt') : snapshot?.stt === 'typing' ? t('call.badge.typing') : t('call.badge.deviceStt')}</span>
      </div>
    </header>

    <div className="callBody">
      <div className="callStage">
        {camera && <figure className="callCamera">
          <video ref={video} autoPlay playsInline muted aria-label={t('call.camera.preview')} />
          <figcaption>{snapshot?.autoFrames ? t('call.camera.autoNote') : t('call.camera.note')}</figcaption>
          <div className="callCameraActions">
            <button type="button" onClick={() => engine.current?.sendFrame()} disabled={snapshot?.frameQueued}><Send aria-hidden="true" />{snapshot?.frameQueued ? t('call.camera.queued') : t('call.camera.send')}</button>
            <label className="callToggle"><input type="checkbox" checked={snapshot?.autoFrames ?? false} onChange={(event) => engine.current?.setAutoFrames(event.target.checked)} />{t('call.camera.auto')}</label>
            <button type="button" className="secondary" onClick={() => void flipCamera()} aria-label={t('call.camera.flip')} title={t('call.camera.flip')}><SwitchCamera aria-hidden="true" /></button>
            {lastFrame && <a className="callSaveFrame" href={lastFrame} download="anum-frame.jpg"><Download aria-hidden="true" />{t('call.camera.save')}</a>}
          </div>
        </figure>}
        <button
          type="button"
          className="callOrb voiceOrb"
          style={{ width: orbSize, height: orbSize }}
          onClick={() => phase === 'speaking' || phase === 'thinking' ? engine.current?.interrupt() : engine.current?.setMuted(!snapshot?.muted)}
          aria-label={phase === 'speaking' || phase === 'thinking' ? t('call.orb.interrupt') : snapshot?.muted ? t('call.control.unmute') : t('call.control.mute')}
        >
          <span className="voiceOrbHalo" aria-hidden="true" />
          <span className="voiceOrbGlow" aria-hidden="true" />
          <Orb3D state={orbState(snapshot)} alert={pending > 0} level={phase === 'hearing' ? 0.6 : 0} size={orbSize} fallback={<><span className="voiceOrbBlob b1" /><span className="voiceOrbBlob b2" /><span className="voiceOrbBlob b3" /></>} />
        </button>
        <p className={`callCaption ${snapshot?.caption ? 'live' : ''}`} dir="auto">{snapshot?.caption || lastAssistant?.text || t('call.hint')}</p>
        {problem && <div className="callProblem" role="alert">
          <ShieldAlert aria-hidden="true" />
          <p>{problemText(problem, caps)}</p>
          {problem.kind === 'typing-only' || problem.kind === 'mic-denied' ? <button type="button" className="secondary" onClick={() => setChatOpen(true)}>{t('call.problem.openChat')}</button> : null}
          {problem.kind === 'limit' || problem.kind === 'session' ? <button type="button" className="secondary" onClick={hangUp}>{t('call.control.end')}</button> : null}
          {cameraProblem ? <button type="button" className="secondary" onClick={() => setCameraProblem(null)} aria-label={t('call.problem.dismiss')}><X aria-hidden="true" /></button> : null}
        </div>}
        {notice && <p className="callNotice" role="status">{notice}</p>}
        {pending > 0 && onOpenApprovals && <button type="button" className="voiceScope" onClick={onOpenApprovals}>{t('call.approvalsWaiting', { count: pending })}</button>}
      </div>

      {chatOpen && <aside className="callChat" aria-label={t('call.chat.title')}>
        <div className="callChatHead"><h3>{t('call.chat.title')}</h3><button type="button" className="callIconButton" onClick={() => setChatOpen(false)} aria-label={t('call.chat.close')}><X aria-hidden="true" /></button></div>
        <ol className="callLog">
          {!snapshot?.turns.length && <li className="callEmpty">{t('call.chat.empty')}</li>}
          {snapshot?.turns.filter((turn) => turn.text).map((turn) => <li key={turn.id} className={`callTurn ${turn.speaker}`}>
            <span className="callSpeaker">{turn.speaker === 'you' ? t('call.chat.you') : name}{turn.withFrame ? ` · ${t('call.chat.withFrame')}` : ''}</span>
            <p dir="auto">{turn.text}</p>
            {turn.meta?.risk_tier === 'confirm' && turn.meta.proposed_task && !turn.resolved && <div className="voiceConfirm">
              <button type="button" onClick={() => void confirmTask(turn.id)}><Check aria-hidden="true" />{t('call.chat.createTask')}</button>
              <button type="button" className="secondary" onClick={() => engine.current?.dismiss(turn.id)}><X aria-hidden="true" />{t('call.chat.notNow')}</button>
            </div>}
            {turn.resolved === 'created' && <span className="voiceResolved">{t('call.chat.taskCreated')}</span>}
            {turn.meta?.risk_tier === 'visual_only' && onOpenApprovals && <div className="voiceConfirm"><button type="button" className="secondary" onClick={onOpenApprovals}><ShieldAlert aria-hidden="true" />{t('call.chat.openApprovals')}</button></div>}
          </li>)}
          <li ref={logEnd} aria-hidden="true" className="callLogEnd" />
        </ol>
        <form className="callComposer" onSubmit={sendTyped}>
          <label className="callDraft"><span className="visuallyHidden">{t('call.chat.message')}</span>
            <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onDraftKey} rows={1} placeholder={t('call.chat.placeholder', { name })} />
          </label>
          <button type="submit" className="callIconButton primary" disabled={!draft.trim()} aria-label={t('call.chat.send')}><ArrowUp aria-hidden="true" /></button>
        </form>
      </aside>}
    </div>

    {moreOpen && <div className="callMore" role="dialog" aria-label={t('call.more.title')}>
      <div className="callChatHead"><h3>{t('call.more.title')}</h3><button type="button" className="callIconButton" onClick={() => setMoreOpen(false)} aria-label={t('call.more.close')}><X aria-hidden="true" /></button></div>
      <dl className="callFactsList">
        <dt>{t('call.settings.language')}</dt><dd lang={locale}>{LOCALE_LABELS[locale] ?? locale}</dd>
        <dt>{t('call.settings.recognition')}</dt><dd>{snapshot?.stt === 'server' && serverStt ? costLine(serverStt, 'stt') : snapshot?.stt === 'typing' ? t('call.badge.typing') : t('call.fact.deviceStt')}</dd>
        <dt>{t('call.settings.voice')}</dt><dd>{tts === 'server' && serverTts ? costLine(serverTts, 'tts') : tts === 'natural' ? t('call.engine.natural') : t('call.fact.deviceTts')}</dd>
        <dt>{t('call.more.latency')}</dt><dd>{latency === null ? t('call.more.latencyNone', { seconds: formatNumber(caps.latency_target_ms.first_audio / 1000) }) : t(`call.more.latency.${latencyVerdict(latency, caps.latency_target_ms.first_audio)}`, { seconds: formatNumber(Math.round(latency / 100) / 10), target: formatNumber(caps.latency_target_ms.first_audio / 1000) })}</dd>
        <dt>{t('call.more.interruptions')}</dt><dd>{formatNumber(snapshot?.bargeIns ?? 0)}</dd>
        <dt>{t('call.settings.retention')}</dt><dd>{t(`call.retention.${retention}`)}</dd>
      </dl>
      <p className="muted">{t('call.fact.approvals')}</p>
      <p className="muted">{t('call.more.privacy')}</p>
    </div>}

    <footer className="callControls">
      <div className="callControlRow">
        <ControlButton pressed={snapshot?.muted ?? false} onClick={() => engine.current?.setMuted(!snapshot?.muted)} label={snapshot?.muted ? t('call.control.unmute') : t('call.control.mute')} short={t('call.control.muteShort')} icon={snapshot?.muted ? <MicOff /> : <Mic />} />
        <ControlButton pressed={Boolean(camera)} onClick={() => void toggleCamera()} label={camera ? t('call.control.videoOff') : t('call.control.videoOn')} short={t('call.control.videoShort')} icon={camera ? <Camera /> : <CameraOff />} />
        <ControlButton pressed={snapshot?.speakerOn ?? true} onClick={() => engine.current?.setSpeaker(!snapshot?.speakerOn)} label={snapshot?.speakerOn ? t('call.control.speakerOff') : t('call.control.speakerOn')} short={t('call.control.speakerShort')} icon={snapshot?.speakerOn ? <Volume2 /> : <VolumeX />} />
        <ControlButton expanded={chatOpen} onClick={() => setChatOpen(!chatOpen)} label={chatOpen ? t('call.chat.close') : t('call.control.chat')} short={t('call.control.chatShort')} icon={<MessageSquareText />} />
        <ControlButton expanded={moreOpen} onClick={() => setMoreOpen(!moreOpen)} label={t('call.control.more')} short={t('call.control.moreShort')} icon={<Ellipsis />} />
      </div>
      <button type="button" className="callEndButton" onClick={hangUp}><PhoneOff aria-hidden="true" />{t('call.control.end')}</button>
    </footer>
  </section>;
}

function costLine(engine: { label: string; model: string | null; usd_per_minute: number | null; usd_per_million_chars: number | null }, kind: 'stt' | 'tts'): string {
  const cost = engineCost({ ...engine, id: 'server', kind: 'server', available: true, reason: null });
  const base = t('call.engine.server', { label: engine.label, model: engine.model ?? '' });
  if (!cost) return t('call.cost.free', { engine: base });
  return kind === 'stt'
    ? t('call.cost.perMinute', { engine: base, price: formatNumber(cost.perMinute ?? 0) })
    : t('call.cost.perMillion', { engine: base, price: formatNumber(cost.perMillionChars ?? 0) });
}

function ControlButton({ pressed, expanded, onClick, label, short, icon }: { pressed?: boolean; expanded?: boolean; onClick: () => void; label: string; short: string; icon: React.ReactNode }) {
  return <button type="button" className="callControl" aria-pressed={pressed} aria-expanded={expanded} aria-label={label} title={label} onClick={onClick}>
    <span className="callControlIcon" aria-hidden="true">{icon}</span>
    <span className="callControlLabel" aria-hidden="true">{short}</span>
  </button>;
}

