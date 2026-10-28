/** Executive PA panel: typed or spoken requests become permissioned tool calls on the server
 * (docs/ANUM_MINE_EXECUTIVE_PA.md). Reads run at once. Anything that changes work is shown as a
 * proposal and runs only after a click on "Confirm and run": a spoken answer never confirms.
 * The assistant has no tool that approves or rejects. */
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { CalendarDays, Compass, Mic, MicOff, Newspaper, Send, ShieldCheck, Route, Users, ListChecks, ClipboardCheck, Sparkles, X } from 'lucide-react';
import type { BuildingProposal } from '@anum/contracts';
import { apiRequest } from './lib/api';
import { describeError } from './lib/errors';
import { formatNumber, t, type MessageKey } from './lib/i18n';
import { createPushToTalk } from './lib/voice';
import {
  createPaClient, parseUtterance, publishNavigation, summaryFigures,
  type Briefing, type InputMode, type Location, type PaResult, type PaTool, type PaToolName, type Tour, type TourStop,
} from './lib/executivePa';
import { parseCampusRequest } from './lib/hqCampusCommand';
import { answerQuestion, createBuildingClient, missingQuestion, parseBuildingRequest, proposalArgs, type BuildingAsk } from './lib/hqBuildingCommand';
import { assignHostAvailable, openAssign, parseAssignSentence, titleFromWork } from './lib/hqAssign';
import { agentClient } from './hq-assign/useAssignAgents';
import { BuildingProposalCard } from './hq-command/BuildingProposalCard';
import { useLocale } from './useLocale';
import type { BriefingOpen } from './PaBriefingCard';
import './executivePanel.css';

// The briefing card loads only when a briefing is asked for (the dock stays small).
// The campus proposal card ("set up my campus") loads when asked for; its button is the only way to create.
const CampusProposalCard = lazy(() => import('./hq-first-run/CampusProposalCard'));
const PaBriefingCard = lazy(() => import('./PaBriefingCard').then((m) => ({ default: m.PaBriefingCard })));

interface Entry { id: number; who: 'you' | 'pa'; text: string; tone?: 'ok' | 'warn' | 'error'; card?: { proposal: BuildingProposal; mode: 'typed' | 'voice'; args: BuildingAsk }; campus?: 'typed' | 'voice' }
interface Pending { tool: PaToolName; args: Record<string, unknown>; proposal: PaResult; mode: InputMode }

const mk = (key: string, vars?: Record<string, string | number>) => t(key as MessageKey, vars);

export function ExecutivePaDock(props: Parameters<typeof ExecutivePanel>[0]) { return <ExecutivePanel {...props} />; }
export function ExecutivePanel({ onOpenView, onLocation, onOpenTask, docked, onStatus }: {
  onOpenView(view: string): void;
  /** In the headquarters' docked panel the shared panel header shows the title and the live status, so this panel leaves them out. */
  docked?: boolean;
  /** Reports whether the tools loaded (the docked panel header turns it into its live status). */
  onStatus?(status: 'loading' | 'ready' | 'unavailable'): void;
  /** Opens one task from a briefing line; without it the Tasks screen opens. */
  onOpenTask?(id: string): void;
  onLocation?(location: Location | null): void;
}) {
  const client = useMemo(() => createPaClient(apiRequest), []);
  const [tools, setTools] = useState<PaTool[] | null>(null);
  const [toolsError, setToolsError] = useState<string | null>(null);
  const [tour, setTour] = useState<Tour | null>(null);
  const [tourError, setTourError] = useState<string | null>(null);
  const [tourIndex, setTourIndex] = useState<number | null>(null);
  const [text, setText] = useState('');
  const [fromVoice, setFromVoice] = useState(false);
  const [listening, setListening] = useState(false);
  const [micNote, setMicNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<Entry[]>([]);
  const [pending, setPending] = useState<Pending | null>(null);
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [briefingBusy, setBriefingBusy] = useState(false);
  const [briefingError, setBriefingError] = useState<string | null>(null);
  const locale = useLocale();
  // The one short question the assistant asked about a building, and what it knows so far.
  const asking = useRef<BuildingAsk | null>(null);
  const [place, setPlace] = useState<string | null>(null);
  const counter = useRef(0);
  const talk = useRef<ReturnType<typeof createPushToTalk> | null>(null);

  const say = useCallback((who: Entry['who'], body: string, tone?: Entry['tone'], card?: Entry['card'], campus?: Entry['campus']) => {
    counter.current += 1;
    setLog((old) => [...old.slice(-49), { id: counter.current, who, text: body, tone, card, campus }]);
  }, []);

  const loadTools = useCallback(async () => {
    setToolsError(null);
    try { setTools(await client.tools()); } catch (error) { setToolsError(t('pa.toolsLoadFailed', { message: describeError(error) })); }
  }, [client]);
  const loadTour = useCallback(async () => {
    setTourError(null);
    try { setTour(await client.tour()); } catch (error) { setTourError(t('pa.tour.loadFailed', { message: describeError(error) })); }
  }, [client]);
  useEffect(() => { void loadTools(); void loadTour(); }, [loadTools, loadTour]);
  useEffect(() => () => talk.current?.cancel(), []);

  const stops: readonly TourStop[] = tour?.stops ?? [];
  const placeOf = useCallback((location: Location): string => {
    const stop = stops.find((s) => s.room_id === location.room_id) ?? stops.find((s) => s.floor_id === location.floor_id) ?? stops.find((s) => s.building_id === location.building_id);
    if (!stop) return location.room_id ?? location.floor_id ?? location.building_id ?? '';
    if (location.room_id) return `${stop.room_name}, ${stop.floor_name}, ${stop.building_name}`;
    if (location.floor_id) return `${stop.floor_name}, ${stop.building_name}`;
    return stop.building_name;
  }, [stops]);

  const present = useCallback((result: PaResult, args: Record<string, unknown>, mode: InputMode) => {
    switch (result.status) {
      case 'needs_confirmation':
        setPending({ tool: result.tool, args, proposal: result, mode });
        say('pa', t('pa.confirmTitle', { tool: mk(`pa.tool.${result.tool}`) }), 'warn');
        return;
      case 'ok': {
        if (result.briefing) {
          setBriefing(result.briefing); setBriefingError(null);
          say('pa', result.briefing.reportable === 0 ? t('pa.briefing.allClear') : t('pa.briefing.title'), 'ok');
        } else if (result.navigate) {
          const label = placeOf(result.navigate);
          publishNavigation(result.navigate);
          onLocation?.(result.navigate);
          setPlace(label);
          say('pa', t('pa.navigated', { place: label }), 'ok');
        } else if (result.open_panel) {
          say('pa', t('pa.panelOpened', { panel: mk(`nav.${result.open_panel.view}`) }), 'ok');
          onOpenView(result.open_panel.view);
        } else if (result.summary) {
          const f = summaryFigures(result.summary);
          const show = (v: number | null) => (v === null ? '-' : formatNumber(v));
          say('pa', t('pa.summary', { tasks: show(f.tasks), blocked: show(f.blocked), approvals: show(f.approvals) }), 'ok');
        } else if (result.tool === 'list_blocked_work') {
          const n = result.items?.length ?? 0;
          say('pa', n === 0 ? t('pa.blockedNone') : [t('pa.blockedSome', { count: formatNumber(n) }), ...(result.items ?? []).map((i) => i.title ?? i.id)].join(' | '), 'ok');
        } else if (result.tool === 'list_pending_approvals') {
          const n = result.items?.length ?? 0;
          say('pa', n === 0 ? t('pa.approvalsNone') : t('pa.approvalsSome', { count: formatNumber(n) }), 'ok');
        }
        return;
      }
      case 'proposal': {
        if (!result.content || !result.payload_hash) { say('pa', t('pa.status.failed', { message: '' }), 'error'); return; }
        asking.current = null;
        say('pa', t('hqBuild.proposalFor', { name: result.content.building.name }), 'ok', {
          proposal: { version: 1, content: result.content, payload_hash: result.payload_hash, bounds: result.bounds ?? { width_m: 60, depth_m: 40 },
            creates: result.creates ?? result.content.totals, can_confirm: result.can_confirm === true, governed_by: result.governed_by ?? '', note: result.note ?? '' },
          mode: mode === 'voice' ? 'voice' : 'typed', args: { name: result.content.building.name, floors: result.content.floors.length },
        });
        return;
      }
      case 'needs_info': {
        asking.current = { name: typeof args.name === 'string' ? args.name : null, floors: typeof args.floors === 'number' ? args.floors : null };
        say('pa', result.missing === 'name' ? t('hqBuild.askName') : t('hqBuild.askFloors', { name: String(args.name ?? '') }), 'warn');
        return;
      }
      case 'invalid': asking.current = null; say('pa', t('hqBuild.invalid', { detail: result.note ?? '' }), 'warn'); return;
      case 'done': say('pa', result.meeting_id ? t('pa.done.meeting') : t('pa.done.task'), 'ok'); return;
      case 'approval_pending': say('pa', t('pa.done.approval'), 'warn'); return;
      case 'denied':
        if (result.tool === 'daily_briefing') setBriefingError(t('pa.briefing.denied'));
        say('pa', t('pa.status.denied'), 'error'); return;
      case 'not_found': say('pa', t('pa.status.not_found'), 'warn'); return;
      case 'not_connected': say('pa', t('pa.status.not_connected'), 'warn'); return;
      case 'not_granted': say('pa', t('pa.status.not_granted'), 'warn'); return;
      case 'stale_confirmation': setPending(null); say('pa', t('pa.status.stale'), 'warn'); return;
      case 'needs_visual_confirmation': say('pa', t('pa.confirmVoice'), 'warn'); return;
      default: say('pa', t('pa.status.failed', { message: result.note ?? '' }), 'error');
    }
  }, [onLocation, onOpenView, placeOf, say]);

  const run = useCallback(async (tool: PaToolName, args: Record<string, unknown>, mode: InputMode, confirmHash?: string) => {
    setBusy(true);
    const isBriefing = tool === 'daily_briefing';
    if (isBriefing) { setBriefingBusy(true); setBriefingError(null); }
    try { present(await client.invoke(tool, args, mode, confirmHash), args, mode); }
    catch (error) {
      if (isBriefing) setBriefingError(t('pa.briefing.failed', { message: describeError(error) }));
      say('pa', t('pa.status.failed', { message: describeError(error) }), 'error');
    }
    finally { setBusy(false); if (isBriefing) setBriefingBusy(false); }
  }, [client, present, say]);

  const proposeBuilding = useCallback((ask: BuildingAsk, mode: InputMode) => {
    const missing = missingQuestion(ask);
    if (missing) {
      asking.current = ask;
      say('pa', missing === 'name' ? t('hqBuild.askName') : t('hqBuild.askFloors', { name: ask.name ?? '' }), 'warn');
      return;
    }
    void run('propose_add_building', proposalArgs(ask, locale), mode);
  }, [locale, run, say]);

  // "ask <agent> to ..." opens the assign dialog pre-filled while the office is open; the dialog's own button is the only way to create.
  const openAssignFor = useCallback(async (parsed: NonNullable<ReturnType<typeof parseAssignSentence>>) => {
    let agentId: string | null = null;
    if (parsed.agentQuery) {
      try {
        const page = await agentClient.list({ category: 'all', q: parsed.agentQuery, status: 'all', limit: 5 });
        const wanted = parsed.agentQuery.toLocaleLowerCase();
        agentId = (page.items.find((a) => a.name.toLocaleLowerCase() === wanted) ?? page.items.find((a) => a.name.toLocaleLowerCase().startsWith(wanted)) ?? (page.items.length === 1 ? page.items[0] : null))?.id ?? null;
      } catch { agentId = null; }
    }
    openAssign({ agentId, title: titleFromWork(parsed.work), instructions: parsed.work, source: 'executive_pa' });
    say('pa', t('hqAssign.paOpened'), 'ok');
  }, [say]);

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    const sentence = text.trim();
    if (!sentence || busy) return;
    const mode: InputMode = fromVoice ? 'voice' : 'typed';
    say('you', sentence);
    setText(''); setFromVoice(false); setPending(null);
    const mode2: InputMode = mode;
    if (parseCampusRequest(sentence)) { say('pa', t('hqFr.cmd.title'), undefined, undefined, mode2 === 'voice' ? 'voice' : 'typed'); return; }
    const building = parseBuildingRequest(sentence);
    if (building) { proposeBuilding(building, mode2); return; }
    if (asking.current) { proposeBuilding(answerQuestion(asking.current, sentence), mode2); return; }
    const assignment = assignHostAvailable() ? parseAssignSentence(sentence) : null;
    if (assignment) { void openAssignFor(assignment); return; }
    const call = parseUtterance(sentence, { stops, lang: locale });
    if (!call) { say('pa', t('pa.notUnderstood'), 'warn'); return; }
    void run(call.tool, call.args, mode);
  };

  const confirm = () => {
    if (!pending?.proposal.payload_hash || busy) return;
    const { tool, args, proposal } = pending;
    setPending(null);
    void run(tool, args, 'click', proposal.payload_hash);
  };

  const toggleMic = () => {
    if (listening) { talk.current?.stop(); return; }
    const session = createPushToTalk(
      (heard, final) => { setText(heard); setFromVoice(true); if (final) say('pa', t('pa.heard', { text: heard })); },
      (message) => { setMicNote(message); setListening(false); },
      document.documentElement.lang === 'ar' ? 'ar-AE' : 'en-US',
      () => setListening(false), false, false,
    );
    talk.current = session;
    if (!session.supported) { setMicNote(t('pa.micOff')); return; }
    setMicNote(null); setListening(true);
    void session.start().then((ok) => { if (!ok) setListening(false); });
  };

  const quick = (label: string, tool: PaToolName, args: Record<string, unknown>) => {
    if (busy) return;
    say('you', label); setPending(null);
    void run(tool, args, 'click');
  };
  const allowed = (name: PaToolName) => tools === null || tools.find((x) => x.name === name)?.allowed !== false;
  const status: 'loading' | 'ready' | 'unavailable' = toolsError ? 'unavailable' : tools === null ? 'loading' : 'ready';
  useEffect(() => { onStatus?.(status); }, [status, onStatus]);
  // Example phrases come from the real office and the real tools, never from invented names.
  const examples = [
    ...(stops[0] ? [t('pa.example.go', { room: stops[0].room_name })] : []),
    t('pa.example.briefing'), t('pa.example.blocked'), t('pa.example.summary'), t('hqBuild.example'),
  ];

  const openBriefingItem = (target: BriefingOpen) => {
    if (target.kind === 'task' && onOpenTask) onOpenTask(target.id);
    else onOpenView(target.view);
  };

  const goStop = (index: number) => {
    const stop = stops[index];
    if (!stop) return;
    setTourIndex(index);
    void run('navigate', { target_kind: 'room', target_id: stop.room_id }, 'click');
  };

  const stop = tourIndex !== null ? stops[tourIndex] : undefined;
  const writeTools = (tools ?? []).filter((x) => x.kind === 'write');
  const readTools = (tools ?? []).filter((x) => x.kind === 'read');

  return <section className="paPanel paDock" aria-labelledby={docked ? undefined : 'pa-title'} data-docked={docked ? 'true' : undefined}>
    <header className="paHead">
      {!docked && <span className="paAvatar" aria-hidden="true"><Sparkles /></span>}
      {!docked && <div className="paHeadText">
        <h2 id="pa-title">{t('pa.title')}</h2>
        <p className={`paStatus ${status}`} role="status"><span aria-hidden="true" />{t(`pa.status.${status}` as MessageKey)}</p>
      </div>}
      <p className="paPlace" aria-live="polite"><Compass aria-hidden="true" /><span>{t('pa.location')}:</span> <strong dir="auto">{place ?? t('pa.location.none')}</strong></p>
    </header>
    <p className="paWelcome">{t('pa.subtitle')}</p>
    <div className="paQuick" role="group" aria-label={t('pa.quick')}>
      <button type="button" className="paPrimary" disabled={busy || stops.length === 0} onClick={() => goStop(0)}><Route aria-hidden="true" />{t('pa.tour.start')}</button>
      <button type="button" className="paPrimary" disabled={busy} onClick={() => quick(t('pa.quick.briefing'), 'daily_briefing', { lang: locale })}><Newspaper aria-hidden="true" />{t('pa.quick.briefing')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => quick(t('pa.quick.calendar'), 'open_panel', { panel: 'calendar' })}><CalendarDays aria-hidden="true" />{t('pa.quick.calendar')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => quick(t('pa.quick.meetings'), 'open_panel', { panel: 'meetings' })}><Users aria-hidden="true" />{t('pa.quick.meetings')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => quick(t('pa.quick.blocked'), 'list_blocked_work', {})}><ListChecks aria-hidden="true" />{t('pa.quick.blocked')}</button>
      <button type="button" className="secondary" disabled={busy || !allowed('list_pending_approvals')} onClick={() => quick(t('pa.quick.approvals'), 'list_pending_approvals', {})}><ClipboardCheck aria-hidden="true" />{t('pa.quick.approvals')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => quick(t('pa.quick.summary'), 'summarise_workspace', {})}><ShieldCheck aria-hidden="true" />{t('pa.quick.summary')}</button>
    </div>
    <div className="paExamples"><p className="muted">{t('pa.examples')}</p>
      <ul>{examples.map((e) => <li key={e}><button type="button" className="linkButton" onClick={() => { setText(e); setFromVoice(false); document.getElementById('pa-input')?.focus(); }}>&ldquo;{e}&rdquo;</button></li>)}</ul>
    </div>

    <form className="paForm" onSubmit={submit}>
      <label htmlFor="pa-input" className="visuallyHidden">{t('pa.inputLabel')}</label>
      <input id="pa-input" value={text} onChange={(e) => { setText(e.target.value); setFromVoice(false); }} placeholder={t('pa.placeholder')} autoComplete="off" dir="auto" />
      <button type="button" className="secondary" aria-pressed={listening} onClick={toggleMic} aria-label={listening ? t('pa.micStop') : t('pa.mic')}>
        {listening ? <MicOff aria-hidden="true" /> : <Mic aria-hidden="true" />}<span>{listening ? t('pa.micStop') : t('pa.mic')}</span>
      </button>
      <button type="submit" disabled={busy || text.trim() === ''}><Send aria-hidden="true" /><span>{busy ? t('pa.working') : t('pa.send')}</span></button>
    </form>
    {micNote && <p className="notice errorNotice" role="alert">{micNote}</p>}

    {pending && <div className="paConfirm" role="group" aria-labelledby="pa-confirm-title">
      <h3 id="pa-confirm-title">{t('pa.confirmTitle', { tool: mk(`pa.tool.${pending.tool}`) })}</h3>
      <dl>{Object.entries(pending.proposal.preview ?? {}).map(([k, v]) => <div key={k}><dt>{k}</dt><dd dir="auto">{String(v)}</dd></div>)}</dl>
      <p className="muted">{t('pa.risk', { risk: mk(`pa.risk.${pending.proposal.risk ?? 'medium'}`) })}. {t('pa.confirmGoverned', { path: pending.proposal.governed_by ?? '' })}</p>
      {pending.mode === 'voice' && <p className="muted">{t('pa.confirmVoice')}</p>}
      <div className="paActions">
        <button type="button" onClick={confirm} disabled={busy}>{t('pa.confirm')}</button>
        <button type="button" className="secondary" onClick={() => setPending(null)}><X aria-hidden="true" />{t('pa.dismiss')}</button>
      </div>
    </div>}

    {(briefingBusy || briefingError || briefing) && <div className="paBriefSlot">
      {briefingBusy && <p className="muted" role="status">{t('pa.working')}</p>}
      {briefingError && <p className="notice errorNotice" role="alert">{briefingError}<button type="button" className="secondary" disabled={busy} onClick={() => void run('daily_briefing', { lang: locale }, 'click')}>{t('common.retry')}</button></p>}
      {briefing && <Suspense fallback={<p className="muted" role="status">{t('pa.working')}</p>}>
        <PaBriefingCard briefing={briefing} busy={busy} onRefresh={() => void run('daily_briefing', { lang: locale }, 'click')} onOpen={openBriefingItem} />
      </Suspense>}
    </div>}

    <div className="paLog" role="log" aria-label={t('pa.log')} aria-live="polite">
      {log.length === 0 && <p className="muted">{t('pa.logEmpty')}</p>}
      {log.map((e) => e.campus
        ? <div key={e.id} className={`paLine ${e.who}`}><strong>{t('pa.assistant')}</strong>
          <Suspense fallback={<p className="muted" role="status">{t('hqFr.loading')}</p>}><CampusProposalCard inputMode={e.campus} onShow={() => { window.location.hash = '#office'; }} /></Suspense></div>
        : e.card
        ? <div key={e.id} className={`paLine ${e.who}`}><strong>{t('pa.assistant')}</strong>
          <BuildingProposalCard proposal={e.card.proposal} inputMode={e.card.mode} onReview={() => proposeBuilding(e.card!.args, 'typed')}
            onShow={(id) => { publishNavigation({ building_id: id }); onLocation?.({ building_id: id }); }} /></div>
        : <p key={e.id} className={`paLine ${e.who} ${e.tone ?? ''}`}><strong>{e.who === 'you' ? t('pa.you') : t('pa.assistant')}</strong><span dir="auto">{e.text}</span></p>)}
    </div>

    <section className="paTour" aria-labelledby="pa-tour-title">
      <h3 id="pa-tour-title"><Route aria-hidden="true" />{t('pa.tour.title')}</h3>
      <p className="muted">{t('pa.tour.text')}</p>
      {tourError && <p className="notice errorNotice" role="alert">{tourError}<button type="button" className="secondary" onClick={() => void loadTour()}>{t('common.retry')}</button></p>}
      {tour?.status === 'not_connected' && <p className="notice" role="status">{t('pa.tour.notConnected')}</p>}
      {tour?.status === 'empty' && <p className="notice" role="status">{t('pa.tour.empty')}</p>}
      {stops.length > 0 && (stop
        ? <div className="paTourNow">
          <p aria-live="polite">{t('pa.tour.progress', { n: formatNumber((tourIndex ?? 0) + 1), total: formatNumber(stops.length), room: stop.room_name, floor: stop.floor_name, building: stop.building_name })}</p>
          <div className="paActions">
            <button type="button" className="secondary" disabled={(tourIndex ?? 0) === 0 || busy} onClick={() => goStop((tourIndex ?? 0) - 1)}>{t('pa.tour.previous')}</button>
            <button type="button" disabled={(tourIndex ?? 0) >= stops.length - 1 || busy} onClick={() => goStop((tourIndex ?? 0) + 1)}>{t('pa.tour.next')}</button>
            <button type="button" className="secondary" onClick={() => setTourIndex(null)}>{t('pa.tour.stop')}</button>
          </div>
        </div>
        : null)}
    </section>

    <details className="paTools" open={toolsError !== null || undefined}><summary id="pa-tools-title">{t('pa.toolsTitle')}</summary>
      {toolsError && <p className="notice errorNotice" role="alert">{toolsError}<button type="button" className="secondary" onClick={() => void loadTools()}>{t('common.retry')}</button></p>}
      <ul>
        {[...readTools, ...writeTools].map((x) => <li key={x.name} className={x.allowed ? '' : 'off'}>
          <span dir="auto">{x.label}</span>
          <small>{x.kind === 'read' ? t('pa.tool.read') : t('pa.tool.write')} | {t('pa.risk', { risk: mk(`pa.risk.${x.risk}`) })}{x.allowed ? '' : ` | ${t('pa.tool.notAllowed')}`}</small>
        </li>)}
      </ul>
      <p className="muted">{t('pa.noApprovalTool')}</p>
    </details>
  </section>;
}
