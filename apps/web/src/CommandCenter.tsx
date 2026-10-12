// ANUM Command (docs/command.md#the-web-command-center): the place to give ANUM an order. Type or
// speak a request (the space orb lives on the Voice page; this page links to it), see the plan
// ANUM would run and edit it, start it, follow every step with its status and result, approve what
// waits, and keep saved quick commands. The overview of the day is Home (HomeView.tsx).
// Every API call goes through lib/commandApi.ts; the pure rules are in lib/commands.ts.
import { useCallback, useEffect, useId, useMemo, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent } from 'react';
import {
  ArrowDown, ArrowRight, ArrowUp, Bot, CircleAlert, CircleCheck, Download, FileText, History, Lightbulb, LoaderCircle, Mic, Paperclip, Pencil, Play,
  Plus, RotateCcw, Save, ShieldCheck, Sparkles, Square, Star, Trash2, X,
} from 'lucide-react';
import type { CommandNextStep, CommandSource, CommandSuggestion, CommandSummary, CommandView, PlanStep, QuickCommand, QuickCommandCategory } from '@anum/contracts';
import { AiAnswer } from './AiAnswer';
import * as api from './lib/api';
import { budgetMessage, describeError, isBudgetExceeded, isPermissionDenied } from './lib/errors';
import { getLocale, t, type MessageKey } from './lib/i18n';
import { riskLabel, statusLabel } from './lib/labels';
import { baseRecognitionMode, createPushToTalk } from './lib/voice';
import { SPEECH_LANGUAGES, SPEECH_LANGUAGE_KEY, SPEECH_LOCALES, initialSpeechLanguage, relativeTime, type SpeechLanguage } from './lib/commandCenter';
import {
  HISTORY_PAGE, MAX_ATTACHMENTS, MAX_REQUEST_CHARS, QUICK_CATEGORIES, canDelete, canStart, commandLocale, commandTone, isLive, moveStep, quickTitle, removeStep,
  stoppableTasks, unstartedTasks,
} from './lib/commands';
import * as commandApi from './lib/commandApi';
import { EmptyPanel, PanelDenied, PanelError, PanelLoading } from './Panels';
import './commandCenter.css';

const storage = (() => { try { return window.localStorage; } catch { return null; } })();
type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'denied' } | { state: 'error'; message: string };
type PlanState = { state: 'idle' } | { state: 'loading'; text: string } | { state: 'ready'; text: string; steps: PlanStep[]; approvals: number } | { state: 'error'; text: string; message: string; denied: boolean };
type Upload = { name: string; progress: number; controller: AbortController };
const POLL_MS = 3000;

export interface CommandCenterProps {
  userName: string | null;
  /** The caller's role here; viewers cannot give commands (the API needs `task:create`). */
  role: string | null;
  /** A request handed over from Home (an insight's "Analyze"); it is planned, never run, until confirmed. */
  initialText?: string | null;
  /** Bumped by the shell on workspace events, so a running command refreshes at once. */
  live: number;
  onGo: (view: 'voice' | 'approvals' | 'tasks' | 'files' | 'home') => void;
  onOpenTask: (taskId: string) => void;
  onOpenFile: (fileId: string) => void;
  onChanged: () => void;
  onBudget: (message: string) => void;
}

export function CommandCenter({ userName, role, initialText, live, onGo, onOpenTask, onOpenFile, onChanged, onBudget }: CommandCenterProps) {
  const viewer = role === 'viewer';
  const [draft, setDraft] = useState(initialText ?? '');
  const [source, setSource] = useState<{ kind: CommandSource; quickId: string | null }>({ kind: initialText ? 'suggestion' : 'typed', quickId: null });
  const [language, setLanguage] = useState<SpeechLanguage>(() => initialSpeechLanguage(storage?.getItem(SPEECH_LANGUAGE_KEY), getLocale()));
  const [listening, setListening] = useState(false);
  const [voiceNote, setVoiceNote] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<commandApi.CommandAttachment[]>([]);
  const [upload, setUpload] = useState<Upload | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [canUpload, setCanUpload] = useState<{ accept: string; allowed: boolean } | null>(null);
  const [plan, setPlan] = useState<PlanState>({ state: 'idle' });
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Load<CommandView> | null>(null);
  const [history, setHistory] = useState<{ items: CommandSummary[]; state: 'loading' | 'ready' | 'error' | 'denied'; message?: string; more: boolean; loadingMore: boolean }>({ items: [], state: 'loading', more: false, loadingMore: false });
  const [chips, setChips] = useState<CommandSuggestion[]>([]);
  const composer = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const talk = useRef<ReturnType<typeof createPushToTalk> | null>(null);
  const heard = useRef<string | null>(null);
  const composerId = useId();
  const voiceAvailable = useMemo(() => baseRecognitionMode() !== 'typing', []);

  // ---- history (paged with the API's cursor) and suggestion chips
  const loadHistory = useCallback(() => {
    setHistory((h) => ({ ...h, state: 'loading' }));
    commandApi.listCommands(HISTORY_PAGE, null)
      .then((items) => setHistory({ items, state: 'ready', more: items.length === HISTORY_PAGE, loadingMore: false }))
      .catch((e: unknown) => setHistory({ items: [], state: isPermissionDenied(e) ? 'denied' : 'error', message: describeError(e), more: false, loadingMore: false }));
  }, []);
  const moreHistory = () => {
    const last = history.items[history.items.length - 1];
    if (!last) return;
    setHistory((h) => ({ ...h, loadingMore: true }));
    commandApi.listCommands(HISTORY_PAGE, last.id)
      .then((items) => setHistory((h) => ({ ...h, items: [...h.items, ...items.filter((x) => !h.items.some((y) => y.id === x.id))], more: items.length === HISTORY_PAGE, loadingMore: false })))
      .catch((e: unknown) => setHistory((h) => ({ ...h, loadingMore: false, state: 'error', message: describeError(e) })));
  };
  const loadChips = useCallback(() => { commandApi.suggestions(getLocale() === 'ar' ? 'ar' : 'en').then(setChips).catch(() => setChips([])); }, []);
  useEffect(() => { loadHistory(); loadChips(); }, [loadHistory, loadChips]);
  useEffect(() => { commandApi.fileUsage().then((u) => setCanUpload({ accept: u.accepted_extensions.join(','), allowed: u.can_upload })).catch(() => setCanUpload(null)); }, []);
  useEffect(() => { composer.current?.focus(); }, []);
  // A request handed over from Home is planned at once (read-only); nothing runs until Start.
  useEffect(() => { if (initialText?.trim() && !viewer) void preview(undefined, initialText.trim()); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => talk.current?.cancel(), []);

  // ---- the selected command's live view: refreshed every few seconds while it runs, and on workspace events
  const refreshDetail = useCallback((id: string, quiet = true) => {
    if (!quiet) setDetail({ state: 'loading' });
    commandApi.getCommand(id).then((value) => {
      setDetail({ state: 'ready', value });
      setHistory((h) => ({ ...h, items: h.items.map((item) => item.id === value.id ? { ...item, status: value.status, progress: value.progress, completedSteps: value.completedSteps, pendingApprovals: value.pendingApprovals } : item) }));
    }).catch((e: unknown) => { if (!quiet) setDetail(isPermissionDenied(e) ? { state: 'denied' } : { state: 'error', message: describeError(e) }); });
  }, []);
  const status = detail?.state === 'ready' ? detail.value.status : null;
  useEffect(() => {
    if (!selected || !status || !(isLive(status))) return;
    const timer = window.setTimeout(() => refreshDetail(selected), POLL_MS);
    return () => window.clearTimeout(timer);
  }, [selected, status, detail, refreshDetail]);
  useEffect(() => { if (selected && live > 0) refreshDetail(selected); }, [live]); // eslint-disable-line react-hooks/exhaustive-deps
  const open = (id: string) => { setSelected(id); refreshDetail(id, false); window.setTimeout(() => document.getElementById('command-detail-title')?.focus(), 0); };
  // With nothing picked yet, the newest command is shown, so the page opens on its results.
  const newest = history.state === 'ready' ? history.items[0]?.id ?? null : null;
  useEffect(() => { if (!selected && newest) { setSelected(newest); refreshDetail(newest, false); } }, [newest]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- voice: push to talk fills the composer; the person checks it and asks for the plan
  const chooseLanguage = (next: SpeechLanguage) => {
    setLanguage(next);
    try { storage?.setItem(SPEECH_LANGUAGE_KEY, next); } catch { /* kept for this page */ }
    if (listening) { talk.current?.cancel(); setListening(false); }
  };
  const toggleListening = () => {
    if (!voiceAvailable) { setVoiceNote(t('command.voice.unsupported')); composer.current?.focus(); return; }
    if (listening) { talk.current?.stop(); return; }
    setVoiceNote(null);
    let last = '';
    talk.current?.cancel();
    talk.current = createPushToTalk(
      (text) => { last = text; heard.current = text; setDraft(text); },
      () => { setListening(false); setVoiceNote(t('command.voice.failed')); },
      SPEECH_LOCALES[language],
      () => { setListening(false); if (last.trim()) { setSource({ kind: 'voice', quickId: null }); setVoiceNote(t('cmd.voice.heard')); } },
      false,
      false,
    );
    try { talk.current.start(); setListening(true); } catch { setListening(false); setVoiceNote(t('command.voice.failed')); }
  };

  // ---- plan, edit, start
  const preview = async (event?: FormEvent, text = draft.trim()) => {
    event?.preventDefault();
    if (!text || upload) { composer.current?.focus(); return; }
    if (listening) { talk.current?.stop(); setListening(false); }
    setStartError(null);
    setPlan({ state: 'loading', text });
    try {
      const result = await commandApi.previewCommand(text, commandLocale(text, getLocale()));
      setPlan({ state: 'ready', text, steps: result.steps, approvals: result.approvalsRequired });
      window.setTimeout(() => document.getElementById('plan-title')?.focus(), 0);
    } catch (e) {
      setPlan({ state: 'error', text, message: describeError(e), denied: isPermissionDenied(e) });
    }
  };
  const use = (text: string, kind: CommandSource, quickId: string | null = null) => {
    setDraft(text); heard.current = null; setSource({ kind, quickId });
    void preview(undefined, text.trim());
  };
  const editPlan = () => { if (plan.state !== 'idle' && plan.state !== 'loading') setDraft(plan.text); setPlan({ state: 'idle' }); window.setTimeout(() => composer.current?.focus(), 0); };
  const start = async () => {
    if (plan.state !== 'ready' || !canStart(plan.steps)) return;
    setStarting(true); setStartError(null);
    const kind = source.kind === 'voice' && heard.current?.trim() !== plan.text ? 'typed' : source.kind;
    try {
      const created = await commandApi.createCommand({
        text: plan.text, steps: plan.steps.map((s) => s.instruction), source: kind, locale: commandLocale(plan.text, getLocale()),
        attachments: attachments.map((a) => a.id), quickCommandId: source.quickId,
      });
      setSelected(created.id);
      setDetail({ state: 'ready', value: created });
      setHistory((h) => ({ ...h, state: h.state === 'loading' ? 'ready' : h.state, items: [created, ...h.items.filter((x) => x.id !== created.id)] }));
      setPlan({ state: 'idle' }); setDraft(''); setAttachments([]); setVoiceNote(null); heard.current = null; setSource({ kind: 'typed', quickId: null });
      try { await commandApi.startSteps(unstartedTasks(created)); } catch (e) {
        if (isBudgetExceeded(e)) onBudget(budgetMessage(e));
        setStartError(t('cmd.start.someFailed', { reason: describeError(e) }));
      }
      refreshDetail(created.id);
      onChanged(); loadChips();
      window.setTimeout(() => document.getElementById('command-detail-title')?.focus(), 0);
    } catch (e) {
      if (isBudgetExceeded(e)) onBudget(budgetMessage(e));
      setStartError(describeError(e));
    } finally { setStarting(false); }
  };
  const onComposerKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void preview(); }
  };

  // ---- documents
  const onPick = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (attachments.length >= MAX_ATTACHMENTS) { setUploadError(t('cmd.attach.tooMany', { count: MAX_ATTACHMENTS })); return; }
    const controller = new AbortController();
    setUpload({ name: file.name, progress: 0, controller });
    setUploadError(null);
    try {
      const record = await commandApi.uploadAttachment(file, (loaded, total) => setUpload((u) => u && { ...u, progress: total ? Math.round((loaded / total) * 100) : 0 }), controller.signal);
      setAttachments((list) => [...list, { id: record.id, name: record.name }]);
    } catch (e) {
      if (!(e instanceof DOMException && e.name === 'AbortError')) setUploadError(t('command.upload.failed', { name: file.name, reason: describeError(e) }));
    } finally { setUpload(null); }
  };

  const planning = plan.state === 'loading';
  const greeting = userName ? t('cmd.subtitleNamed', { name: userName }) : t('cmd.subtitle');

  return <div className="commandCenter">
    <header className="commandHeader">
      <div><h1 id="view-title">{t('cmd.title')}</h1><p>{greeting}</p></div>
      <button type="button" className="secondary voiceLink" onClick={() => onGo('voice')}><Mic aria-hidden="true" />{t('cmd.openVoice')}</button>
    </header>

    {viewer
      ? <section className="surface commandPanel"><PanelDenied title={t('cmd.viewer.title')} text={t('cmd.viewer.text')} /></section>
      : <form className="surface commandComposer" onSubmit={(event) => void preview(event)} aria-labelledby="composer-title">
        <h2 id="composer-title" className="composerTitle">{t('cmd.composer.title')}</h2>
        <div className="composerRow">
          <button type="button" className={`composerMic ${listening ? 'on' : ''}`} aria-pressed={listening} aria-label={listening ? t('command.voice.stop') : t('command.voice.start')} title={listening ? t('command.voice.stop') : t('command.voice.start')} onClick={toggleListening}><Mic aria-hidden="true" /></button>
          <label className="visuallyHidden" htmlFor={composerId}>{t('command.inputLabel')}</label>
          <textarea id={composerId} ref={composer} rows={2} value={draft} dir="auto" placeholder={t('cmd.placeholder')} maxLength={MAX_REQUEST_CHARS}
            onChange={(event) => { setDraft(event.target.value); if (source.kind !== 'typed' && source.kind !== 'voice') setSource({ kind: 'typed', quickId: null }); }} onKeyDown={onComposerKey} />
          <div className="composerTools">
            {canUpload?.allowed !== false && <button type="button" className="toolButton" aria-label={t('command.attach')} title={t('command.attach')} disabled={Boolean(upload) || attachments.length >= MAX_ATTACHMENTS} onClick={() => picker.current?.click()}><Paperclip aria-hidden="true" /></button>}
            <input ref={picker} type="file" hidden accept={canUpload?.accept || undefined} onChange={(event) => void onPick(event)} tabIndex={-1} aria-hidden="true" />
          </div>
          <button type="submit" id={`${composerId}-send`} className="sendButton" aria-label={t('cmd.preview')} title={t('cmd.preview')} disabled={!draft.trim() || planning || Boolean(upload)}>
            {planning ? <LoaderCircle className="spin" aria-hidden="true" /> : <ArrowRight className="flipRtl" aria-hidden="true" />}
          </button>
        </div>
        <div className="composerMeta">
          <div className="languagePills" role="group" aria-label={t('command.language')}>
            {SPEECH_LANGUAGES.map((code) => <button key={code} type="button" lang={code} aria-pressed={language === code} className={language === code ? 'on' : ''} onClick={() => chooseLanguage(code)}>{t(`command.language.${code}` as MessageKey)}</button>)}
          </div>
          <p className="composerHint">{t('cmd.hint')}</p>
        </div>
        {(attachments.length > 0 || upload) && <ul className="attachList" aria-label={t('command.attachments')}>
          {attachments.map((file) => <li key={file.id}><FileText aria-hidden="true" /><span dir="auto">{file.name}</span><button type="button" aria-label={t('command.removeAttachment', { name: file.name })} onClick={() => setAttachments((list) => list.filter((x) => x.id !== file.id))}><X aria-hidden="true" /></button></li>)}
          {upload && <li className="uploading" role="status"><LoaderCircle className="spin" aria-hidden="true" /><span dir="auto">{t('command.upload.progress', { name: upload.name, percent: upload.progress })}</span><button type="button" aria-label={t('command.upload.cancel')} onClick={() => upload.controller.abort()}><X aria-hidden="true" /></button></li>}
        </ul>}
        {uploadError && <p className="notice errorNotice" role="alert"><CircleAlert aria-hidden="true" />{uploadError}</p>}
        {voiceNote && <p className="composerNote" role="status">{voiceNote}</p>}
        {listening && <p className="composerNote" role="status">{t('command.orb.listening')}</p>}
        {chips.length > 0 && <ul className="quickChips" aria-label={t('cmd.chips')}>{chips.map((chip, i) => <li key={`${chip.kind}-${i}`}><button type="button" className="quickChip" title={chip.text} onClick={() => use(chip.text, chip.kind === 'quick' ? 'quick_command' : 'suggestion', chip.quickCommandId)}>
          {chip.kind === 'quick' ? <Star aria-hidden="true" /> : chip.kind === 'recent' ? <RotateCcw aria-hidden="true" /> : <Lightbulb aria-hidden="true" />}<span dir="auto">{chip.title}</span>
        </button></li>)}</ul>}
      </form>}

    {plan.state !== 'idle' && <PlanPanel plan={plan} starting={starting} error={startError} attachments={attachments.length}
      onChange={(steps) => setPlan((p) => p.state === 'ready' ? { ...p, steps, approvals: steps.filter((s) => s.approval === 'required').length } : p)}
      onEdit={editPlan} onStart={() => void start()} onRetry={() => void preview(undefined, plan.text)} onCancel={() => { setPlan({ state: 'idle' }); setStartError(null); }} />}
    {plan.state === 'idle' && startError && <PanelError message={startError} />}

    <div className="commandGrid split">
      <HistoryPanel history={history} selected={selected} onOpen={open} onMore={moreHistory} onRetry={loadHistory} onFocusComposer={() => composer.current?.focus()} viewer={viewer} />
      <DetailPanel detail={detail} onRetry={() => selected && refreshDetail(selected, false)} onOpenTask={onOpenTask} onOpenFile={onOpenFile} onGo={onGo}
        onRefresh={() => selected && refreshDetail(selected)} onBudget={onBudget}
        onDeleted={(id) => { setSelected(null); setDetail(null); setHistory((h) => ({ ...h, items: h.items.filter((x) => x.id !== id) })); }}
        onUse={(text) => use(text, 'next_step')} onChanged={onChanged} onSaved={loadChips} viewer={viewer} />
    </div>

    <QuickCommandsPanel viewer={viewer} onUse={(q) => use(q.text, 'quick_command', q.id)} onChanged={loadChips} />
  </div>;
}

// ---------------------------------------------------------------- the plan

function PlanPanel({ plan, starting, error, attachments, onChange, onEdit, onStart, onRetry, onCancel }: {
  plan: Exclude<PlanState, { state: 'idle' }>; starting: boolean; error: string | null; attachments: number;
  onChange: (steps: PlanStep[]) => void; onEdit: () => void; onStart: () => void; onRetry: () => void; onCancel: () => void;
}) {
  return <section className="surface commandPanel planPanel" aria-labelledby="plan-title" aria-busy={plan.state === 'loading'}>
    <div className="panelHead"><h2 id="plan-title" tabIndex={-1}>{t('cmd.plan.title')}</h2>
      <button type="button" className="iconButton small" aria-label={t('cmd.plan.cancel')} title={t('cmd.plan.cancel')} onClick={onCancel}><X aria-hidden="true" /></button></div>
    <p className="planRequest" dir="auto">{plan.text}</p>
    {plan.state === 'loading' && <PanelLoading label={t('cmd.plan.loading')} />}
    {plan.state === 'error' && (plan.denied
      ? <PanelDenied title={t('cmd.viewer.title')} text={t('cmd.viewer.text')} />
      : <PanelError message={t('cmd.plan.failed', { reason: plan.message })} onRetry={onRetry} />)}
    {plan.state === 'ready' && <>
      <p className="planSummary">{t('cmd.plan.summary', { steps: plan.steps.length, approvals: plan.approvals })}{attachments > 0 && <> · {t('cmd.plan.attachments', { count: attachments })}</>}</p>
      {plan.steps.length === 0
        ? <EmptyPanel icon={<Bot />} title={t('cmd.plan.emptyTitle')} text={t('cmd.plan.emptyText')} action={t('cmd.plan.edit')} onAction={onEdit} />
        : <ol className="planSteps">{plan.steps.map((step, index) => <li key={`${step.position}-${step.instruction}`} className={`planStep ${step.approval}`}>
          <span className="planNumber" aria-hidden="true">{index + 1}</span>
          <div className="planBody">
            <strong dir="auto">{step.title}</strong>
            <div className="planTags">
              <span className="planAgent"><Bot aria-hidden="true" />{step.agent.name}</span>
              {step.tool && <code dir="ltr">{step.tool.name}</code>}
              <span className={`riskBadge risk-${step.riskLevel}`}>{riskLabel(step.riskLevel)}</span>
              <span className={`statusBadge ${step.approval === 'blocked' ? 'dangerTone' : step.approval === 'required' ? 'warning' : 'success'}`}>{t(`cmd.approval.${step.approval}` as MessageKey)}</span>
            </div>
            {step.approval !== 'none' && <small dir="auto">{step.approvalReason}</small>}
            {step.note && <small className="planNote" dir="auto">{step.note}</small>}
          </div>
          <div className="planTools">
            <button type="button" className="iconButton small" disabled={index === 0} aria-label={t('cmd.plan.up', { position: index + 1 })} title={t('cmd.plan.upShort')} onClick={() => onChange(moveStep(plan.steps, index, -1))}><ArrowUp aria-hidden="true" /></button>
            <button type="button" className="iconButton small" disabled={index === plan.steps.length - 1} aria-label={t('cmd.plan.down', { position: index + 1 })} title={t('cmd.plan.downShort')} onClick={() => onChange(moveStep(plan.steps, index, 1))}><ArrowDown aria-hidden="true" /></button>
            <button type="button" className="iconButton small" disabled={plan.steps.length === 1} aria-label={t('cmd.plan.remove', { position: index + 1 })} title={t('cmd.plan.removeShort')} onClick={() => onChange(removeStep(plan.steps, index))}><Trash2 aria-hidden="true" /></button>
          </div>
        </li>)}</ol>}
      {plan.steps.some((s) => s.approval === 'blocked') && <p className="approvalWarning" role="alert">{t('cmd.plan.blocked')}</p>}
      {error && <PanelError message={error} />}
      <div className="planActions">
        <button type="button" className="secondary" onClick={onEdit}><Pencil aria-hidden="true" />{t('cmd.plan.edit')}</button>
        <button type="button" className="primaryButton" disabled={starting || !canStart(plan.steps)} onClick={onStart}>{starting ? <LoaderCircle className="spin" aria-hidden="true" /> : <Play aria-hidden="true" />}{starting ? t('cmd.plan.starting') : t('cmd.plan.start')}</button>
      </div>
    </>}
  </section>;
}

// ---------------------------------------------------------------- running and recent commands

function HistoryPanel({ history, selected, onOpen, onMore, onRetry, onFocusComposer, viewer }: {
  history: { items: CommandSummary[]; state: 'loading' | 'ready' | 'error' | 'denied'; message?: string; more: boolean; loadingMore: boolean };
  selected: string | null; onOpen: (id: string) => void; onMore: () => void; onRetry: () => void; onFocusComposer: () => void; viewer: boolean;
}) {
  const running = history.items.filter((c) => isLive(c.status) || c.status === 'waiting_approval').length;
  return <section className="surface commandPanel" aria-labelledby="history-title">
    <div className="panelHead"><h2 id="history-title"><History aria-hidden="true" />{t('cmd.history.title')}</h2>{running > 0 && <span className="activePill">{t('cmd.history.running', { count: running })}</span>}</div>
    {history.state === 'loading' && history.items.length === 0 && <PanelLoading />}
    {history.state === 'error' && <PanelError message={t('cmd.history.failed', { reason: history.message ?? '' })} onRetry={onRetry} />}
    {history.state === 'denied' && <PanelDenied title={t('cmd.denied.title')} text={t('cmd.denied.text')} />}
    {history.state === 'ready' && history.items.length === 0 && <EmptyPanel icon={<Sparkles />} title={t('cmd.history.emptyTitle')} text={viewer ? t('cmd.history.emptyViewer') : t('cmd.history.emptyText')} action={viewer ? undefined : t('cmd.history.emptyAction')} onAction={onFocusComposer} />}
    {history.items.length > 0 && <ul className="rowList light">{history.items.map((command) => <li key={command.id}><button type="button" className={`listRow ${selected === command.id ? 'selected' : ''}`} aria-current={selected === command.id ? 'true' : undefined} onClick={() => onOpen(command.id)}>
      <span className={`rowIcon ${commandTone(command.status)}`} aria-hidden="true">{isLive(command.status) ? <LoaderCircle /> : command.status === 'completed' ? <CircleCheck /> : command.status === 'waiting_approval' ? <ShieldCheck /> : <Sparkles />}</span>
      <span className="rowText"><strong dir="auto">{command.text}</strong><small>{t('cmd.history.meta', { done: command.completedSteps, steps: command.stepCount, when: relativeTime(command.createdAt) })}</small>
        {isLive(command.status) && <span className="progress mini" role="progressbar" aria-label={t('cmd.detail.progress')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(command.progress * 100)}><span style={{ width: `${Math.round(command.progress * 100)}%` }} /></span>}</span>
      <span className={`statusBadge ${commandTone(command.status)}`}>{t(`cmd.status.${command.status}` as MessageKey)}</span>
    </button></li>)}</ul>}
    {history.more && <button type="button" className="linkButton showMore" disabled={history.loadingMore} onClick={onMore}>{history.loadingMore ? t('common.loading') : t('cmd.history.more')}</button>}
  </section>;
}

function DetailPanel({ detail, onRetry, onOpenTask, onOpenFile, onGo, onRefresh, onBudget, onDeleted, onUse, onChanged, onSaved, viewer }: {
  detail: Load<CommandView> | null; onRetry: () => void; onOpenTask: (id: string) => void; onOpenFile: (id: string) => void; onGo: CommandCenterProps['onGo'];
  onRefresh: () => void; onBudget: (message: string) => void; onDeleted: (id: string) => void; onUse: (text: string) => void; onChanged: () => void; onSaved: () => void; viewer: boolean;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [next, setNext] = useState<CommandNextStep[] | null>(null);
  const [saved, setSaved] = useState(false);
  const view = detail?.state === 'ready' ? detail.value : null;
  const settled = view ? !isLive(view.status) : false;
  useEffect(() => { setProblem(null); setSaved(false); }, [view?.id]);
  useEffect(() => {
    if (!view || !settled) { setNext(null); return; }
    let live = true;
    commandApi.nextSteps(view.id).then((items) => { if (live) setNext(items); }).catch(() => { if (live) setNext([]); });
    return () => { live = false; };
  }, [view?.id, view?.status, settled]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!detail) return <section className="surface commandPanel" aria-labelledby="command-detail-title">
    <div className="panelHead"><h2 id="command-detail-title" tabIndex={-1}>{t('cmd.detail.title')}</h2></div>
    <EmptyPanel icon={<Bot />} title={t('cmd.detail.noneTitle')} text={t('cmd.detail.noneText')} />
  </section>;

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key); setProblem(null);
    try { await fn(); onRefresh(); onChanged(); } catch (e) { if (isBudgetExceeded(e)) onBudget(budgetMessage(e)); setProblem(describeError(e)); } finally { setBusy(null); }
  };
  const approveAll = (command: CommandView) => act('approve', async () => {
    const refusals: string[] = [];
    // One request per approval, each with the payload hash shown (docs/command.md#approve-all): a refusal does not stop the others.
    for (const approval of command.pending) {
      try { await api.approveTask(approval); } catch (e) { if (isBudgetExceeded(e)) onBudget(budgetMessage(e)); refusals.push(`${approval.action}: ${describeError(e)}`); }
    }
    if (refusals.length) throw new Error(t('cmd.detail.approveRefused', { reasons: refusals.join(' · ') }));
  });
  const saveQuick = (command: CommandView) => act('save', async () => { await commandApi.createQuick({ title: quickTitle(command.text), text: command.text, category: 'custom' }); setSaved(true); onSaved(); });
  const onNext = (item: CommandNextStep, command: CommandView) => {
    if (item.kind === 'review_approvals') onGo('approvals');
    else if (item.kind === 'save_quick_command') void saveQuick(command);
    else if (item.text) onUse(item.text);
  };

  return <section className="surface commandPanel detailPanel" aria-labelledby="command-detail-title" aria-busy={detail.state === 'loading'}>
    <div className="panelHead"><h2 id="command-detail-title" tabIndex={-1}>{view ? t(isLive(view.status) ? 'cmd.detail.running' : 'cmd.detail.title') : t('cmd.detail.title')}</h2>
      {view && <span className={`statusBadge ${commandTone(view.status)}`}>{t(`cmd.status.${view.status}` as MessageKey)}</span>}</div>
    {detail.state === 'loading' && <PanelLoading />}
    {detail.state === 'error' && <PanelError message={t('cmd.detail.failed', { reason: detail.message })} onRetry={onRetry} />}
    {detail.state === 'denied' && <PanelDenied title={t('cmd.denied.title')} text={t('cmd.denied.text')} />}
    {view && <>
      <p className="planRequest" dir="auto">{view.text}</p>
      <div className="progress" role="progressbar" aria-label={t('cmd.detail.progress')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(view.progress * 100)}><span style={{ width: `${Math.round(view.progress * 100)}%` }} /></div>
      <p className="planSummary" role="status">{t('cmd.detail.summary', { done: view.completedSteps, steps: view.stepCount })}{view.durationSeconds !== null && <> · {t('command.seconds', { count: Math.round(view.durationSeconds) })}</>}</p>
      {view.pending.length > 0 && <div className="approvalStrip" role="group" aria-label={t('cmd.detail.approvals')}>
        <p><ShieldCheck aria-hidden="true" />{t('cmd.detail.waiting', { count: view.pending.length })}</p>
        <div><button type="button" className="primaryButton smallButton" disabled={busy !== null || viewer} onClick={() => void approveAll(view)}>{t('cmd.detail.approveAll')}</button>
          <button type="button" className="secondary smallButton" onClick={() => onGo('approvals')}>{t('cmd.detail.reviewEach')}</button></div>
      </div>}
      <ol className="stepList">{view.steps.map((step) => <li key={step.taskId} className="stepItem">
        <div className="stepTop"><span className="planNumber" aria-hidden="true">{step.position}</span><strong dir="auto">{step.title}</strong><span className={`statusBadge ${commandTone(step.status)}`}>{step.status === 'missing' ? t('cmd.step.missing') : statusLabel(step.status)}</span></div>
        <small className="stepMeta"><Bot aria-hidden="true" />{step.agent.name}{step.tool && <> · <code dir="ltr">{step.tool}</code></>}</small>
        {(step.status === 'running' || step.status === 'queued') && <div className="progress mini" role="progressbar" aria-label={t('cmd.step.progress', { position: step.position })} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(step.progress * 100)}><span style={{ width: `${Math.round(step.progress * 100)}%` }} /></div>}
        {step.result && <div className="trackerResult"><AiAnswer text={step.result} /></div>}
        <button type="button" className="linkButton" onClick={() => onOpenTask(step.taskId)}>{t('cmd.step.open')}</button>
      </li>)}</ol>
      {view.files.some((f) => f.kind === 'attachment' && f.fileId) && <div className="detailFiles"><h3>{t('cmd.detail.files')}</h3><ul>{view.files.filter((f) => f.kind === 'attachment' && f.fileId).map((file) => <li key={file.path}><button type="button" className="linkButton" onClick={() => onOpenFile(file.fileId!)}><FileText aria-hidden="true" /><span dir="auto">{file.name}</span></button></li>)}</ul></div>}
      {problem && <PanelError message={problem} />}
      {settled && next && next.length > 0 && <div className="nextSteps"><h3>{t('cmd.detail.next')}</h3><ul className="quickChips">{next.map((item) => <li key={item.id}><button type="button" className="quickChip" disabled={(item.kind === 'save_quick_command' && saved) || (viewer && item.kind !== 'review_approvals')} onClick={() => onNext(item, view)}>
        {item.kind === 'save_quick_command' ? <Save aria-hidden="true" /> : item.kind === 'review_approvals' ? <ShieldCheck aria-hidden="true" /> : <Sparkles aria-hidden="true" />}<span dir="auto">{item.kind === 'save_quick_command' && saved ? t('cmd.detail.saved') : item.label}</span>
      </button></li>)}</ul></div>}
      <div className="planActions">
        {stoppableTasks(view).length > 0 && !viewer && <button type="button" className="stopButton" disabled={busy !== null} onClick={() => void act('stop', () => commandApi.stopTasks(stoppableTasks(view)))}><Square aria-hidden="true" />{busy === 'stop' ? t('command.tracker.stopping') : t('cmd.detail.stop')}</button>}
        <button type="button" className="secondary smallButton" disabled={busy !== null} onClick={() => void act('report', () => commandApi.downloadReport(view.id))}><Download aria-hidden="true" />{t('cmd.detail.report')}</button>
        {canDelete(view.status) && <button type="button" className="secondary smallButton" disabled={busy !== null} onClick={() => { setBusy('delete'); commandApi.deleteCommand(view.id).then(() => onDeleted(view.id)).catch((e: unknown) => setProblem(describeError(e))).finally(() => setBusy(null)); }}><Trash2 aria-hidden="true" />{t('cmd.detail.delete')}</button>}
      </div>
    </>}
  </section>;
}

// ---------------------------------------------------------------- saved and favourite commands

function QuickCommandsPanel({ viewer, onUse, onChanged }: { viewer: boolean; onUse: (q: QuickCommand) => void; onChanged: () => void }) {
  const [items, setItems] = useState<Load<QuickCommand[]>>({ state: 'loading' });
  const [category, setCategory] = useState<QuickCommandCategory | 'all'>('all');
  const [form, setForm] = useState<{ id: string | null; title: string; text: string; category: QuickCommandCategory } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const formId = useId();
  const load = useCallback(() => { setItems({ state: 'loading' }); commandApi.listQuick().then((value) => setItems({ state: 'ready', value })).catch((e: unknown) => setItems(isPermissionDenied(e) ? { state: 'denied' } : { state: 'error', message: describeError(e) })); }, []);
  useEffect(load, [load]);
  const list = items.state === 'ready' ? items.value.filter((q) => category === 'all' || q.category === category).sort((a, b) => QUICK_CATEGORIES.indexOf(a.category) - QUICK_CATEGORIES.indexOf(b.category) || a.position - b.position) : [];
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!form) return;
    const title = form.title.trim(), text = form.text.trim();
    if (!title || !text) { setProblem(t('cmd.quick.needBoth')); return; }
    setSaving(true); setProblem(null);
    try {
      if (form.id) await commandApi.updateQuick(form.id, { title, text, category: form.category });
      else await commandApi.createQuick({ title, text, category: form.category });
      setForm(null); load(); onChanged();
    } catch (e) { setProblem(describeError(e)); } finally { setSaving(false); }
  };
  const remove = (q: QuickCommand) => { setProblem(null); commandApi.deleteQuick(q.id).then(() => { load(); onChanged(); }).catch((e: unknown) => setProblem(describeError(e))); };
  return <section className="surface commandPanel" aria-labelledby="quick-title">
    <div className="panelHead"><h2 id="quick-title"><Star aria-hidden="true" />{t('cmd.quick.title')}</h2>
      {!viewer && items.state === 'ready' && !form && <button type="button" className="secondary smallButton" onClick={() => setForm({ id: null, title: '', text: '', category: category === 'all' ? 'custom' : category })}><Plus aria-hidden="true" />{t('cmd.quick.add')}</button>}</div>
    <div className="segmented" role="group" aria-label={t('cmd.quick.filter')}>
      {(['all', ...QUICK_CATEGORIES] as const).map((key) => <button key={key} type="button" aria-pressed={category === key} className={category === key ? 'on' : ''} onClick={() => setCategory(key)}>{t(`cmd.quick.cat.${key}` as MessageKey)}</button>)}
    </div>
    {form && <form className="quickForm" onSubmit={(e) => void save(e)} aria-label={form.id ? t('cmd.quick.editTitle') : t('cmd.quick.add')}>
      <label htmlFor={`${formId}-title`}><span>{t('cmd.quick.name')}</span><input id={`${formId}-title`} maxLength={80} dir="auto" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
      <label htmlFor={`${formId}-cat`}><span>{t('cmd.quick.category')}</span><select id={`${formId}-cat`} value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value as QuickCommandCategory })}>{QUICK_CATEGORIES.map((c) => <option key={c} value={c}>{t(`cmd.quick.cat.${c}` as MessageKey)}</option>)}</select></label>
      <label htmlFor={`${formId}-text`} className="wide"><span>{t('cmd.quick.text')}</span><textarea id={`${formId}-text`} rows={2} maxLength={2000} dir="auto" value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} /></label>
      <div className="planActions wide"><button type="button" className="secondary smallButton" onClick={() => { setForm(null); setProblem(null); }}>{t('common.cancel')}</button><button type="submit" className="primaryButton smallButton" disabled={saving}><Save aria-hidden="true" />{t('cmd.quick.save')}</button></div>
    </form>}
    {problem && <PanelError message={problem} />}
    {items.state === 'loading' && <PanelLoading />}
    {items.state === 'error' && <PanelError message={t('cmd.quick.failed', { reason: items.message })} onRetry={load} />}
    {items.state === 'denied' && <PanelDenied title={t('cmd.denied.title')} text={t('cmd.denied.text')} />}
    {items.state === 'ready' && list.length === 0 && !form && <EmptyPanel icon={<Star />} title={t('cmd.quick.emptyTitle')} text={viewer ? t('cmd.quick.emptyViewer') : t('cmd.quick.emptyText')} action={viewer ? undefined : t('cmd.quick.add')} onAction={() => setForm({ id: null, title: '', text: '', category: category === 'all' ? 'custom' : category })} />}
    {list.length > 0 && <ul className="quickGrid">{list.map((q) => <li key={q.id} className="quickCard">
      <div className="quickCardTop"><strong dir="auto">{q.title}</strong><span className="countPill">{t(`cmd.quick.cat.${q.category}` as MessageKey)}</span></div>
      <p dir="auto">{q.text}</p>
      <small>{q.useCount ? t('cmd.quick.used', { count: q.useCount }) : t('cmd.quick.unused')}</small>
      <div className="quickCardActions">
        {!viewer && <button type="button" className="primaryButton smallButton" onClick={() => onUse(q)}><Play aria-hidden="true" />{t('cmd.quick.use')}</button>}
        {!viewer && <button type="button" className="iconButton small" aria-label={t('cmd.quick.editNamed', { title: q.title })} title={t('cmd.quick.editTitle')} onClick={() => setForm({ id: q.id, title: q.title, text: q.text, category: q.category })}><Pencil aria-hidden="true" /></button>}
        {!viewer && <button type="button" className="iconButton small" aria-label={t('cmd.quick.deleteNamed', { title: q.title })} title={t('cmd.quick.delete')} onClick={() => remove(q)}><Trash2 aria-hidden="true" /></button>}
      </div>
    </li>)}</ul>}
  </section>;
}

