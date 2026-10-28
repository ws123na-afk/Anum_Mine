/** The "Assign work" dialog of the headquarters (spec: docs/ANUM_MINE_ASSIGN_WORK.md). It sends one request to
 * `POST /api/v1/office/assignments`, which runs the Tasks page's own create and run code, so every permission,
 * budget, autonomy and approval rule applies. It never navigates away. */
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, CheckCircle2, Loader2, ShieldCheck, WifiOff, X } from 'lucide-react';
import type { AgentProfile, AssignResponse } from '@anum/contracts';
import * as api from '../lib/api';
import { ApiError, describeError } from '../lib/errors';
import { t, type MessageKey } from '../lib/i18n';
import { useModal } from '../Dialog';
import { useLocale } from '../useLocale';
import {
  availability, CONTEXT_MAX, createAssignClient, emptyDraft, EXPECTED_MAX, INSTRUCTIONS_MAX, newAttemptKey, riskView, TITLE_MAX,
  toRequest, validateDraft, type AssignDraft, type AssignErrors, type AssignPrefill,
} from '../lib/hqAssign';
import { agentClient, useAssignAgents } from './useAssignAgents';
import './hqAssign.css';
import { LocalDateInput } from '../hq-date/LocalDateInput.tsx';

const client = createAssignClient(api.apiRequest);
const mk = (key: string, vars?: Record<string, string | number>) => t(key as MessageKey, vars);

export interface AssignDialogProps {
  prefill: AssignPrefill;
  /** Agents seated in the office the dialog was opened from, when known. They are listed first. */
  seatedIds?: ReadonlySet<string>;
  onClose: () => void;
  onAssigned: (result: AssignResponse, agent: AgentProfile) => void;
  onOpenTask: (taskId: string) => void;
  onNavigate: (hash: string) => void;
}

export function AssignDialog({ prefill, seatedIds, onClose, onAssigned, onOpenTask, onNavigate }: AssignDialogProps) {
  const locale = useLocale();
  const panel = useModal(onClose);
  const ids = { title: useId(), agent: useId(), search: useId(), name: useId(), instr: useId(), exp: useId(), ctx: useId(), pri: useId(), due: useId() };
  const list = useAssignAgents(prefill.agentId ?? null);
  const [draft, setDraft] = useState<AssignDraft>(() => emptyDraft(prefill));
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ detail: string; paused: boolean } | null>(null);
  const [done, setDone] = useState<{ result: AssignResponse; agent: AgentProfile } | null>(null);
  const [online, setOnline] = useState(typeof navigator === 'undefined' ? true : navigator.onLine);
  const [turnOn, setTurnOn] = useState<'idle' | 'busy' | 'failed'>('idle');
  const attempt = useRef<{ key: string; body: string } | null>(null);

  useEffect(() => {
    const up = () => setOnline(true); const down = () => setOnline(false);
    window.addEventListener('online', up); window.addEventListener('offline', down);
    return () => { window.removeEventListener('online', up); window.removeEventListener('offline', down); };
  }, []);

  // With no agent chosen yet, the first one seated in this office (when the dialog came from a room) is offered.
  useEffect(() => {
    if (draft.agentId || list.status !== 'ready' || !seatedIds?.size) return;
    const first = list.items.find((a) => seatedIds.has(a.id) && a.status === 'active') ?? list.items.find((a) => seatedIds.has(a.id));
    if (first) setDraft((d) => (d.agentId ? d : { ...d, agentId: first.id }));
  }, [list.status, list.items, seatedIds, draft.agentId]);

  const agent = list.items.find((a) => a.id === draft.agentId) ?? null;
  const agentLoading = list.status === 'loading' && !agent;
  const state = draft.agentId && !agentLoading ? availability(agent, true) : null;
  const errors: AssignErrors = useMemo(() => validateDraft(draft), [draft]);
  const errorCount = Object.keys(errors).length;
  const risk = agent ? riskView(agent, list.catalog) : null;
  const ordered = useMemo(() => {
    const seated = (a: AgentProfile) => (seatedIds?.has(a.id) ? 0 : 1);
    return [...list.items].sort((a, b) => seated(a) - seated(b) || a.name.localeCompare(b.name));
  }, [list.items, seatedIds]);
  const set = <K extends keyof AssignDraft>(key: K, value: AssignDraft[K]) => { setDraft((d) => ({ ...d, [key]: value })); setFailure(null); };
  const canSend = !busy && online && state?.kind === 'ready' && errorCount === 0;

  const send = async () => {
    setTouched(true);
    if (!canSend || !agent) return;
    const body = toRequest(draft, prefill.source ?? 'other');
    const text = JSON.stringify(body);
    // A retry of the same attempt keeps its key, so a lost answer never makes a second task.
    if (!attempt.current || attempt.current.body !== text) attempt.current = { key: newAttemptKey(), body: text };
    setBusy(true); setFailure(null);
    try {
      const result = await client.assign(body, attempt.current.key);
      setDone({ result, agent });
      onAssigned(result, agent);
    } catch (e) {
      const paused = e instanceof ApiError && e.status === 409 && /paused/i.test(e.detail ?? '');
      setFailure({ detail: describeError(e), paused });
      if (paused) list.replace({ ...agent, status: 'paused', can_run: false });
    } finally { setBusy(false); }
  };

  const turnAgentOn = async () => {
    if (!agent) return;
    setTurnOn('busy');
    try {
      const next = await agentClient.update(agent.id, { status: 'active', version: agent.version });
      list.replace(next); setTurnOn('idle'); setFailure(null);
    } catch { setTurnOn('failed'); }
  };

  const submitLabel = busy ? t('hqAssign.sending') : draft.run ? t('hqAssign.submit') : t('hqAssign.submitOnly');
  const title = agent ? t('hqAssign.titleFor', { name: agent.name }) : t('hqAssign.title');
  const err = (key: keyof AssignErrors) => (touched && errors[key] ? mk(errors[key] as string) : null);

  return createPortal(<div className="hqAsBackdrop" dir={locale === 'ar' ? 'rtl' : 'ltr'} onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
    <div className="hqAsDialog" role="dialog" aria-modal="true" aria-labelledby={ids.title} ref={panel} aria-busy={busy}>
      <header className="hqAsHead">
        <h2 id={ids.title}>{done ? t('hqAssign.done.title', { name: done.agent.name }) : title}</h2>
        <button type="button" className="hqAsX" aria-label={t('hqAssign.close')} onClick={onClose} disabled={busy}><X size={16} aria-hidden="true" /></button>
      </header>
      {done ? <DoneView done={done} onOpenTask={() => { onOpenTask(done.result.task.id); onClose(); }} onNavigate={(h) => { onNavigate(h); onClose(); }} onClose={onClose}
        onAnother={() => { setDone(null); attempt.current = null; setDraft((d) => ({ ...emptyDraft(prefill), agentId: d.agentId })); setTouched(false); }} />
        : <form className="hqAsBody" noValidate onSubmit={(e) => { e.preventDefault(); void send(); }}>
          <p className="hqAsNote">{t('hqAssign.intro')}</p>
          {!online && <p className="hqAsBox hqAsWarn" role="alert"><WifiOff size={14} aria-hidden="true" /> {t('hqAssign.state.offline')}</p>}

          <section className="hqAsSection" aria-labelledby={`${ids.agent}-h`}>
            <h3 id={`${ids.agent}-h`}>{t('hqAssign.agent')}</h3>
            {list.status === 'loading' && list.items.length === 0 && <p className="hqAsNote" role="status"><Loader2 size={14} className="hqAsSpin" aria-hidden="true" /> {t('hqAssign.agentsLoading')}</p>}
            {list.status === 'error' && <div className="hqAsBox hqAsWarn" role="alert"><p>{t('hqAssign.agentsError', { detail: list.error })}</p><button type="button" onClick={() => void list.retry()}>{t('hqAssign.retry')}</button></div>}
            {list.status === 'ready' && list.items.length === 0 && !list.q && <div className="hqAsBox" role="status">
              <p>{t('hqAssign.agentsEmpty')}</p><button type="button" onClick={() => { onNavigate('#agents'); onClose(); }}>{t('hqAssign.agentsEmptyAction')}</button></div>}
            {(list.items.length > 0 || list.q) && <>
              <label htmlFor={ids.search} className="hqAsLabel">{t('hqAssign.agentSearch')}</label>
              <input id={ids.search} type="search" value={list.q} onChange={(e) => list.setQ(e.target.value)} dir="auto" autoComplete="off" />
              <label htmlFor={ids.agent} className="hqAsLabel">{t('hqAssign.agent')}</label>
              <select id={ids.agent} value={draft.agentId} onChange={(e) => { set('agentId', e.target.value); setTurnOn('idle'); }} aria-invalid={Boolean(err('agent'))} aria-describedby={err('agent') ? `${ids.agent}-e` : undefined}>
                <option value="">{t('hqAssign.agentPick')}</option>
                {ordered.map((a) => <option key={a.id} value={a.id}>
                  {a.name}{a.department ? ` (${a.department})` : ''}{a.status === 'paused' ? ` - ${t('hqAssign.paused')}` : ''}{seatedIds?.has(a.id) && prefill.roomName ? ` - ${t('hqAssign.seatedHere', { room: prefill.roomName })}` : ''}</option>)}
              </select>
              {err('agent') && <p id={`${ids.agent}-e`} className="hqAsErr" role="alert">{err('agent')}</p>}
              {list.hasMore && <button type="button" className="hqAsLink" onClick={() => void list.more()}>{t('hqAssign.agentsMore')}</button>}
            </>}
            <AgentState state={state} agent={agent} turnOn={turnOn} onTurnOn={() => void turnAgentOn()} />
          </section>

          <section className="hqAsSection">
            <Field id={ids.name} label={t('hqAssign.field.title')} error={err('title')} count={`${draft.title.length}/${TITLE_MAX}`}>
              <input id={ids.name} value={draft.title} maxLength={TITLE_MAX + 40} onChange={(e) => set('title', e.target.value)} dir="auto" aria-invalid={Boolean(err('title'))} aria-describedby={err('title') ? `${ids.name}-e` : undefined} required />
            </Field>
            <Field id={ids.instr} label={t('hqAssign.field.instructions')} error={err('instructions')} count={t('hqAssign.hint.count', { used: draft.instructions.length, max: INSTRUCTIONS_MAX })}>
              <textarea id={ids.instr} rows={4} value={draft.instructions} onChange={(e) => set('instructions', e.target.value)} dir="auto" aria-invalid={Boolean(err('instructions'))} aria-describedby={err('instructions') ? `${ids.instr}-e` : undefined} required />
            </Field>
            <Field id={ids.exp} label={t('hqAssign.field.expected')} hint={t('hqAssign.hint.expected')} error={err('expected')} count={t('hqAssign.hint.count', { used: draft.expectedResult.length, max: EXPECTED_MAX })}>
              <textarea id={ids.exp} rows={2} value={draft.expectedResult} onChange={(e) => set('expectedResult', e.target.value)} dir="auto" aria-describedby={`${ids.exp}-h`} />
            </Field>
            <Field id={ids.ctx} label={t('hqAssign.field.context')} hint={t('hqAssign.hint.context')} error={err('context')} count={t('hqAssign.hint.count', { used: draft.contextNote.length, max: CONTEXT_MAX })}>
              <input id={ids.ctx} value={draft.contextNote} onChange={(e) => set('contextNote', e.target.value)} dir="auto" aria-describedby={`${ids.ctx}-h`} />
            </Field>
            <div className="hqAsRow">
              <div className="hqAsField">
                <label htmlFor={ids.pri} className="hqAsLabel">{t('hqAssign.field.priority')}</label>
                <select id={ids.pri} value={draft.priority} onChange={(e) => set('priority', e.target.value as AssignDraft['priority'])}>
                  {(['low', 'medium', 'high'] as const).map((p) => <option key={p} value={p}>{mk(`hqAssign.priority.${p}`)}</option>)}
                </select>
              </div>
              <div className="hqAsField">
                <label htmlFor={ids.due} className="hqAsLabel">{t('hqAssign.field.due')}</label>
                <LocalDateInput id={ids.due} type="datetime-local" value={draft.due} onChange={(e) => set('due', e.target.value)} aria-invalid={Boolean(err('due'))} aria-describedby={err('due') ? `${ids.due}-e` : undefined} />
                {err('due') && <p id={`${ids.due}-e`} className="hqAsErr" role="alert">{err('due')}</p>}
              </div>
            </div>
            <label className="hqAsCheck"><input type="checkbox" checked={draft.run} onChange={(e) => set('run', e.target.checked)} /> <span>{t('hqAssign.run')}</span></label>
            <p className="hqAsNote">{t('hqAssign.runHint')}</p>
          </section>

          <RiskSection agent={agent} risk={risk} catalogFailed={list.catalogFailed} catalogLoaded={Boolean(list.catalog)} />

          {failure && <div className="hqAsBox hqAsFail" role="alert">
            <p>{failure.paused ? t('hqAssign.failedPaused') : t('hqAssign.failed', { detail: failure.detail })}</p>
            <p>{t('hqAssign.failedKeep')}</p>
          </div>}
          {touched && errorCount > 0 && <p className="hqAsErr" role="status">{t('hqAssign.summary', { count: errorCount })}</p>}
          <footer className="hqAsFoot">
            <button type="submit" className="hqAsPrimary" disabled={busy || !online || (state !== null && state.kind !== 'ready')} data-autofocus>{submitLabel}</button>
            <button type="button" onClick={onClose} disabled={busy}>{t('hqAssign.cancel')}</button>
          </footer>
        </form>}
    </div>
  </div>, document.body);
}

function Field({ id, label, hint, error, count, children }: { id: string; label: string; hint?: string; error: string | null; count?: string; children: ReactNode }) {
  return <div className="hqAsField">
    <div className="hqAsLabelRow"><label htmlFor={id} className="hqAsLabel">{label}</label>{count && <small className="hqAsCount">{count}</small>}</div>
    {children}
    {hint && <p id={`${id}-h`} className="hqAsNote">{hint}</p>}
    {error && <p id={`${id}-e`} className="hqAsErr" role="alert">{error}</p>}
  </div>;
}

function AgentState({ state, agent, turnOn, onTurnOn }: { state: ReturnType<typeof availability> | null; agent: AgentProfile | null; turnOn: 'idle' | 'busy' | 'failed'; onTurnOn: () => void }) {
  if (!state || state.kind === 'ready') return null;
  if (state.kind === 'paused') {
    return <div className="hqAsBox hqAsWarn" role="status">
      <p><AlertTriangle size={14} aria-hidden="true" /> <strong>{t('hqAssign.state.paused')}</strong></p>
      <p>{state.canTurnOn ? t('hqAssign.state.pausedOwner') : t('hqAssign.state.pausedOther')}</p>
      {state.canTurnOn && agent && <button type="button" onClick={onTurnOn} disabled={turnOn === 'busy'}>{turnOn === 'busy' ? t('hqAssign.state.turningOn') : t('hqAssign.state.turnOn')}</button>}
      {turnOn === 'failed' && <p role="alert">{t('hqAssign.failed', { detail: '' })}</p>}
    </div>;
  }
  return <p className="hqAsBox hqAsWarn" role="alert"><AlertTriangle size={14} aria-hidden="true" /> {state.kind === 'denied' ? t('hqAssign.state.denied') : t('hqAssign.state.unavailable')}</p>;
}

function RiskSection({ agent, risk, catalogFailed, catalogLoaded }: { agent: AgentProfile | null; risk: ReturnType<typeof riskView> | null; catalogFailed: boolean; catalogLoaded: boolean }) {
  if (!agent || !risk) return null;
  const level = (l: 'auto' | 'ask' | 'off') => mk(`hqAssign.risk.level.${l}`);
  const all = [...risk.read.tools, ...risk.write.tools, ...risk.external.tools];
  return <section className="hqAsSection" aria-labelledby="hqAsRisk-h" aria-live="polite">
    <h3 id="hqAsRisk-h"><ShieldCheck size={14} aria-hidden="true" /> {t('hqAssign.risk.title')}</h3>
    <ul className="hqAsRisk">
      <li data-level={risk.read.level}>{t('hqAssign.risk.read', { level: level(risk.read.level) })}</li>
      <li data-level={risk.write.level}>{t('hqAssign.risk.write', { level: level(risk.write.level) })}</li>
      <li data-level={risk.external.level}>{t('hqAssign.risk.external', { level: level(risk.external.level) })}</li>
    </ul>
    <p className="hqAsNote">{all.length ? t('hqAssign.risk.tools', { tools: all.join(', ') }) : catalogLoaded ? t('hqAssign.risk.noTools') : ''}</p>
    {risk.highRisk.length > 0 && <p className="hqAsBox hqAsWarn">{t('hqAssign.risk.high', { tools: risk.highRisk.join(', ') })}</p>}
    {risk.unavailable.length > 0 && <p className="hqAsNote">{t('hqAssign.risk.unavailable', { tools: risk.unavailable.join(', ') })}</p>}
    {catalogFailed && <p className="hqAsNote">{t('hqAssign.risk.catalogMissing')}</p>}
    <p className="hqAsNote">{t('hqAssign.risk.approval')}</p>
  </section>;
}

function DoneView({ done, onOpenTask, onNavigate, onClose, onAnother }: { done: { result: AssignResponse; agent: AgentProfile }; onOpenTask: () => void; onNavigate: (h: string) => void; onClose: () => void; onAnother: () => void }) {
  const { result } = done;
  const started = result.run !== null;
  return <div className="hqAsBody hqAsDone" role="status">
    <p><CheckCircle2 size={15} aria-hidden="true" /> {t('hqAssign.done.created', { title: result.task.title })}</p>
    {result.replayed && <p>{t('hqAssign.done.replayed')}</p>}
    {result.approval ? <p>{t('hqAssign.done.waiting')}</p> : result.run_error ? <p role="alert">{t('hqAssign.done.notStarted', { detail: result.run_error.detail })}</p>
      : started ? <p>{t('hqAssign.done.started')}</p> : <p>{t('hqAssign.done.queued')}</p>}
    <p className="hqAsNote">{t('hqAssign.done.inFlow')}</p>
    <div className="hqAsFoot">
      <button type="button" className="hqAsPrimary" onClick={onOpenTask} data-autofocus>{t('hqAssign.done.openTask')}</button>
      {result.approval && <button type="button" onClick={() => onNavigate('#approvals')}>{t('hqAssign.done.openApprovals')}</button>}
      <button type="button" onClick={onAnother}>{t('hqAssign.done.another')}</button>
      <button type="button" onClick={onClose}>{t('hqAssign.close')}</button>
    </div>
  </div>;
}
