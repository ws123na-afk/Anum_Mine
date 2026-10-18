// Small shared pieces of the Phone calls page (CallsView.tsx): notices, the pager, the call link and
// the log-a-call dialog that opens when the phone's own dialer is used.
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { CircleDot, Phone, RefreshCw } from 'lucide-react';
import { Modal } from './Dialog';
import { describeError } from './lib/errors';
import { formatNumber, t } from './lib/i18n';
import {
  directionLabel, durationText, elapsedSeconds, isDialable, LOG_OUTCOMES, outcomeLabel, parseDuration, telHref,
  type ApiCall, type ApiContact, type Direction, type Outcome,
} from './lib/calls';
import * as callsApi from './lib/callsApi';

export function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" /><span>{children}</span></p>;
}

export function ErrorNotice({ message, onRetry }: { message: string; onRetry(): void }) {
  return <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{message}</span><button type="button" className="secondary" onClick={onRetry}><RefreshCw aria-hidden="true" />{t('common.retry')}</button></div>;
}

/** Previous and next with the range shown ("26 to 50 of 80"). */
export function Pager({ offset, limit, total, onOffset }: { offset: number; limit: number; total: number; onOffset(next: number): void }) {
  if (total <= limit) return null;
  return <nav className="callsPager" aria-label={t('calls.pager')}>
    <button type="button" className="secondary" disabled={offset <= 0} onClick={() => onOffset(Math.max(0, offset - limit))}>{t('calls.previous')}</button>
    <span className="muted">{t('calls.range', { from: formatNumber(offset + 1), to: formatNumber(Math.min(total, offset + limit)), total: formatNumber(total) })}</span>
    <button type="button" className="secondary" disabled={offset + limit >= total} onClick={() => onOffset(offset + limit)}>{t('calls.next')}</button>
  </nav>;
}

export interface DialTarget { phone: string; contact?: ApiContact | null }

/**
 * "Call on your phone": a real `tel:` link (the phone's dialer, or a computer's phone app), and
 * the log dialog opens at the same moment so the call can be logged on return.
 */
export function CallLink({ target, onDial, label, className = 'buttonLink' }: { target: DialTarget; onDial(target: DialTarget): void; label?: string; className?: string }) {
  const href = telHref(target.phone);
  if (!href) return null;
  return <a className={className} href={href} onClick={() => onDial(target)}><Phone aria-hidden="true" />{label ?? t('calls.callOnPhone')}</a>;
}

/** After the dialer: direction, outcome, duration (from when the dialer opened, editable) and notes. */
export function LogCallDialog({ target, startedAt, contacts, onClose, onLogged }: {
  target: DialTarget | null; startedAt: number | null; contacts: ApiContact[]; onClose(): void; onLogged(call: ApiCall, summarize: boolean): void;
}) {
  const ids = useId();
  const [contactId, setContactId] = useState(target?.contact?.id ?? '');
  const [phone, setPhone] = useState(target?.phone ?? '');
  const [direction, setDirection] = useState<Direction>('outbound');
  const [outcome, setOutcome] = useState<Outcome>('answered');
  const [duration, setDuration] = useState(() => startedAt ? durationText(elapsedSeconds(startedAt, Date.now())) : '');
  const edited = useRef(false);
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);

  // Coming back from the dialer: the duration follows the time away until the person edits it.
  useEffect(() => {
    if (!startedAt) return undefined;
    const update = () => { if (!edited.current && document.visibilityState === 'visible') setDuration(durationText(elapsedSeconds(startedAt, Date.now()))); };
    window.addEventListener('focus', update);
    document.addEventListener('visibilitychange', update);
    return () => { window.removeEventListener('focus', update); document.removeEventListener('visibilitychange', update); };
  }, [startedAt]);

  const picked = contacts.find((item) => item.id === contactId) ?? target?.contact ?? null;
  async function save(event: FormEvent, summarize: boolean) {
    event.preventDefault();
    setProblem(null);
    const seconds = duration.trim() ? parseDuration(duration) : null;
    if (duration.trim() && seconds === null) { setProblem(t('calls.log.badDuration')); return; }
    if (!contactId && !isDialable(phone)) { setProblem(t('calls.log.needNumber')); return; }
    setBusy(true);
    try {
      const started = startedAt ? new Date(startedAt).toISOString() : undefined;
      const call = await callsApi.logCall({
        ...(contactId ? { contact_id: contactId } : {}), ...(phone && (!contactId || phone !== picked?.phone) ? { phone } : {}),
        direction, outcome, duration_seconds: seconds, ...(started ? { started_at: started } : {}), ...(notes.trim() ? { notes: notes.trim() } : {}),
      });
      onLogged(call, summarize && Boolean(notes.trim()));
    } catch (e) { setProblem(describeError(e)); } finally { setBusy(false); }
  }

  return <Modal title={picked ? t('calls.log.with', { name: picked.name }) : t('calls.log.title')} className="formDialog callsLogDialog" onClose={onClose}>
    <form className="formGrid callsForm" onSubmit={(event) => void save(event, false)}>
      <label className="field"><span>{t('calls.log.contact')}</span>
        <select value={contactId} onChange={(event) => { setContactId(event.target.value); const next = contacts.find((item) => item.id === event.target.value); if (next) setPhone(next.phone); }}>
          <option value="">{t('calls.log.noContact')}</option>
          {target?.contact && !contacts.some((item) => item.id === target.contact?.id) && <option value={target.contact.id}>{target.contact.name}</option>}
          {contacts.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      <label className="field"><span>{t('calls.log.number')}</span><input value={phone} inputMode="tel" dir="ltr" maxLength={40} autoComplete="off" onChange={(event) => setPhone(event.target.value)} /></label>
      <fieldset className="field callsChoice"><legend>{t('calls.log.direction')}</legend>
        {(['outbound', 'inbound'] as const).map((value) => <label key={value}><input type="radio" name={`${ids}-direction`} checked={direction === value} onChange={() => setDirection(value)} />{directionLabel(value)}</label>)}
      </fieldset>
      <fieldset className="field callsChoice"><legend>{t('calls.log.outcome')}</legend>
        {LOG_OUTCOMES.map((value) => <label key={value}><input type="radio" name={`${ids}-outcome`} checked={outcome === value} onChange={() => setOutcome(value)} />{outcomeLabel(value)}</label>)}
      </fieldset>
      <label className="field"><span>{t('calls.log.duration')}</span>
        <input value={duration} dir="ltr" inputMode="numeric" maxLength={9} aria-describedby={`${ids}-duration`} onChange={(event) => { edited.current = true; setDuration(event.target.value); }} />
        <small id={`${ids}-duration`} className="muted">{t('calls.log.durationHint')}</small>
      </label>
      <label className="field callsWide"><span>{t('calls.log.notes')}</span><textarea value={notes} dir="auto" maxLength={8000} rows={4} placeholder={t('calls.notes.placeholder')} onChange={(event) => setNotes(event.target.value)} /></label>
      {problem && <div className="callsWide"><Notice error>{problem}</Notice></div>}
      <div className="actions callsWide">
        <button type="submit" disabled={busy} data-autofocus>{busy ? t('calls.saving') : t('calls.log.save')}</button>
        <button type="button" className="secondary" disabled={busy || !notes.trim()} onClick={(event) => void save(event, true)}>{t('calls.log.saveSummarize')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={onClose}>{t('calls.log.skip')}</button>
      </div>
    </form>
  </Modal>;
}
