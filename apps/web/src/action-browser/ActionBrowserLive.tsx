// The live view of a task's browser (docs/action-browser.md#live-view), in the Tasks page detail and
// the headquarters task panel. Lazy chunk. While the task runs or waits it refreshes the session
// and, for owners, the masked picture about once a second; Stop closes the browser and cancels the
// task. When the page needs a person, an owner can click on the picture, type and press keys.
import { useCallback, useEffect, useId, useRef, useState, type MouseEvent } from 'react';
import { Globe, KeyRound, Square } from 'lucide-react';
import { useLocale } from '../useLocale';
import { describeError } from '../lib/errors';
import { formatDateTime } from '../lib/i18n';
import { OWNER_KEYS, POLL_MS, isActive, say, statusText, stepText, toPagePoint, type BrowserSession } from '../lib/actionBrowser';
import { fetchFrame, sendInput, sessionsForTask, stopSession } from './api';
import './actionBrowser.css';

export default function ActionBrowserLive({ taskId, pulse, onChanged }: { taskId: string; pulse?: number | string; onChanged?: () => void }) {
  const locale = useLocale();
  const [session, setSession] = useState<BrowserSession | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'none' | 'error'>('loading');
  const [error, setError] = useState('');
  const alive = useRef(true);
  const load = useCallback(async () => {
    try {
      const found = await sessionsForTask(taskId);
      if (!alive.current) return;
      const latest = found[found.length - 1] ?? null;
      setSession(latest); setState(latest ? 'ready' : 'none'); setError('');
    } catch (e) {
      if (!alive.current) return;
      setError(describeError(e)); setState((s) => (s === 'ready' ? s : 'error'));
    }
  }, [taskId]);
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; }; }, [load, pulse]);
  const active = isActive(session);
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => { if (document.visibilityState !== 'hidden') void load(); }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, load]);

  const headingId = useId();
  if (state === 'loading') return <section className="abLive" aria-label={say('liveRegion')}><p className="abMuted" role="status">{say('loadingLive')}</p></section>;
  if (state === 'error') return <section className="abLive" aria-label={say('liveRegion')}><p className="abNote abError" role="alert">{say('liveFailed', { error })}</p><button type="button" className="secondary" onClick={() => void load()}>{say('retry')}</button></section>;
  if (!session) return null;
  const tone = session.status === 'completed' ? 'success' : session.status === 'failed' || session.status === 'stopped' ? 'dangerTone' : 'warning';
  return <section className="abLive" aria-labelledby={headingId} lang={locale} data-action-browser-live={session.status}>
    <div className="abLiveHead">
      <h3 id={headingId}><Globe aria-hidden="true" />{say('liveTitle')}</h3>
      <span className={`statusBadge ${tone}`} aria-live="polite">{statusText(session)}</span>
      {session.can_stop && <StopButton session={session} onStopped={(next) => { setSession(next); onChanged?.(); }} />}
    </div>
    {session.current_url && <p className="abUrl" dir="ltr" title={session.current_url}>{session.current_url}</p>}
    <Picture session={session} />
    {session.waiting === 'handoff' && session.can_watch && session.live && <Handoff session={session} />}
    <Scope session={session} />
    <Steps session={session} />
    {session.status === 'completed' && session.message && <div className="abResult"><h4>{say('answerTitle')}</h4><p dir="auto" className="abPre">{session.message}</p></div>}
    {(session.status === 'failed' || session.status === 'stopped') && session.message && <p className="abNote" role="status" dir="auto">{session.message}</p>}
  </section>;
}

function StopButton({ session, onStopped }: { session: BrowserSession; onStopped(next: BrowserSession): void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stop = async () => {
    setBusy(true); setError(null);
    try { onStopped(await stopSession(session.id)); setAsking(false); }
    catch (e) { setError(describeError(e)); }
    finally { setBusy(false); }
  };
  if (!asking) return <button type="button" className="secondary abStop" onClick={() => setAsking(true)}><Square aria-hidden="true" />{say('stop')}</button>;
  return <span className="abStopAsk" role="group" aria-label={say('stopAsk')}>
    <span>{say('stopAsk')}</span>
    <button type="button" className="danger" disabled={busy} onClick={() => void stop()} data-autofocus>{busy ? say('stopping') : say('stopYes')}</button>
    <button type="button" className="secondary" onClick={() => setAsking(false)}>{say('cancel')}</button>
    {error && <span role="alert" className="abError">{error}</span>}
  </span>;
}

export function Picture({ session, interval = POLL_MS }: { session: BrowserSession; interval?: number }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const live = session.live && session.can_watch;
  const handoff = session.waiting === 'handoff';
  useEffect(() => {
    if (!live) { setUrl(null); return undefined; }
    let stopped = false;
    let current: string | null = null;
    const tick = async () => {
      if (document.visibilityState === 'hidden') return;
      try {
        const next = await fetchFrame(session.id);
        if (stopped) { if (next) URL.revokeObjectURL(next); return; }
        if (current) URL.revokeObjectURL(current);
        current = next; setUrl(next); setFailed(false);
      } catch { if (!stopped) setFailed(true); }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), interval);
    return () => { stopped = true; window.clearInterval(timer); if (current) URL.revokeObjectURL(current); };
  }, [live, session.id, interval]);
  if (!session.can_watch) return <p className="abNote" role="note">{say('pictureMembers')}</p>;
  if (!live) return <p className="abMuted">{say('noPicture')}</p>;
  const click = async (event: MouseEvent<HTMLButtonElement>) => {
    const point = toPagePoint(event.clientX, event.clientY, event.currentTarget.getBoundingClientRect());
    if (!point) return;
    try { await sendInput(session.id, { kind: 'click', ...point }); setHint(null); } catch (e) { setHint(say('inputFailed', { error: describeError(e) })); }
  };
  const image = url ? <img src={url} alt={say('pictureAlt', { url: session.current_url ?? '' })} width={1280} height={800} /> : <span className="abFrameEmpty">{failed ? say('pictureFailed') : say('loadingLive')}</span>;
  return <figure className="abFrame">
    {handoff ? <button type="button" className="abFrameButton" onClick={(e) => void click(e)} aria-label={say('pictureAlt', { url: session.current_url ?? '' })}>{image}</button> : image}
    <figcaption className="abMuted">{say('pictureMasked')}</figcaption>
    {hint && <p className="abNote abError" role="alert">{hint}</p>}
  </figure>;
}

function Handoff({ session }: { session: BrowserSession }) {
  const [text, setText] = useState('');
  const [note, setNote] = useState<string | null>(null);
  const id = useId();
  const send = async (event: Parameters<typeof sendInput>[1]) => {
    try { await sendInput(session.id, event); setNote(null); } catch (e) { setNote(say('inputFailed', { error: describeError(e) })); }
  };
  return <div className="abHandoff" role="region" aria-labelledby={`${id}-h`}>
    <h4 id={`${id}-h`}><KeyRound aria-hidden="true" />{say('handoffTitle')}</h4>
    <p>{say('handoffText')}</p>
    <form className="abHandoffForm" onSubmit={(e) => { e.preventDefault(); if (text) { void send({ kind: 'text', text }); setText(''); } }}>
      <label className="field"><span>{say('typeHere')}</span><input dir="auto" value={text} maxLength={200} autoComplete="off" onChange={(e) => setText(e.target.value)} /></label>
      <button type="submit" disabled={!text}>{say('sendText')}</button>
    </form>
    <div className="abKeys">{OWNER_KEYS.map((key) => <button key={key} type="button" className="secondary" onClick={() => void send({ kind: 'key', key })}>{say('key', { key })}</button>)}</div>
    {note && <p className="abNote abError" role="alert">{note}</p>}
  </div>;
}

function Scope({ session }: { session: BrowserSession }) {
  const handles = [...session.logins.map((h) => `login:${h}`), ...session.cards.map((h) => `card:${h}`)];
  return <dl className="abScope">
    <div><dt>{say('sites')}</dt><dd>{session.sites.map((site) => <span key={site} className="abChip" dir="ltr">{site}</span>)}</dd></div>
    {handles.length > 0 && <div><dt>{say('handles')}</dt><dd>{handles.map((h) => <span key={h} className="abChip" dir="ltr">{h}</span>)}</dd></div>}
  </dl>;
}

function Steps({ session }: { session: BrowserSession }) {
  const id = useId();
  return <div className="abSteps">
    <h4 id={id}>{say('stepsTitle')}</h4>
    {session.steps.length === 0
      ? <p className="abMuted">{say('noSteps')}</p>
      : <ol aria-labelledby={id}>{session.steps.slice(-60).map((step) => <li key={step.seq} data-kind={step.kind}>
        <time dateTime={step.created_at}>{formatDateTime(step.created_at, { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time>
        <span dir="auto">{stepText(step)}</span>
      </li>)}</ol>}
  </div>;
}
