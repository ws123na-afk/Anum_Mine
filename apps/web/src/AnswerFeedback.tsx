import { useId, useState, type FormEvent } from 'react';
import { Check, PenLine, ThumbsDown, ThumbsUp } from 'lucide-react';
import { describeError } from './lib/errors';
import { t } from './lib/i18n';
import * as learningApi from './lib/learningApi';
import './learning.css';

export type Rating = 'up' | 'down';

/**
 * Feedback on one Assistant answer (docs/learning.md): Helpful / Not helpful (with an optional
 * reason), and "Edit to teach": the answer rewritten the way the user would have written it,
 * which ANUM learns their style from. Saved only while learning is on; the server says so otherwise.
 */
export function AnswerFeedback({ turnId, content, rating, onRated }: { turnId: string; content: string; rating: Rating | null; onRated: (rating: Rating) => void }) {
  const [mode, setMode] = useState<'reason' | 'teach' | null>(null);
  const [reason, setReason] = useState(''), [edited, setEdited] = useState(content);
  const [busy, setBusy] = useState(false), [note, setNote] = useState<{ error: boolean; text: string } | null>(null);
  const id = useId();

  async function send(body: Parameters<typeof learningApi.sendFeedback>[0], done: string): Promise<boolean> {
    setBusy(true); setNote(null);
    try {
      const result = await learningApi.sendFeedback(body);
      setNote(result.recorded ? { error: false, text: done } : { error: true, text: result.message ?? t('feedback.notRecorded') });
      return result.recorded;
    } catch (e) { setNote({ error: true, text: t('feedback.failed', { reason: describeError(e) }) }); return false; }
    finally { setBusy(false); }
  }
  async function rate(next: Rating) {
    if (await send({ surface: 'chat', subject_id: turnId, rating: next }, t('feedback.thanks'))) {
      onRated(next);
      setMode(next === 'down' ? 'reason' : null);
    }
  }
  async function sendReason(event: FormEvent) {
    event.preventDefault();
    if (reason.trim() && await send({ surface: 'chat', subject_id: turnId, rating: 'down', reason: reason.trim() }, t('feedback.thanks'))) { setMode(null); setReason(''); }
  }
  async function teach(event: FormEvent) {
    event.preventDefault();
    if (edited.trim() === content.trim()) { setNote({ error: true, text: t('feedback.unchanged') }); return; }
    if (await send({ surface: 'chat', subject_id: turnId, original: content, final: edited }, t('feedback.teachSaved'))) setMode(null);
  }

  return <div className="chatFeedbackWrap">
    <div className="chatFeedback" role="group" aria-label={t('feedback.group')}>
      <button type="button" className="iconButton" aria-pressed={rating === 'up'} aria-label={t('feedback.helpful')} title={t('feedback.helpful')} disabled={busy} onClick={() => void rate('up')}><ThumbsUp aria-hidden="true" /></button>
      <button type="button" className="iconButton" aria-pressed={rating === 'down'} aria-label={t('feedback.notHelpful')} title={t('feedback.notHelpful')} disabled={busy} onClick={() => void rate('down')}><ThumbsDown aria-hidden="true" /></button>
      <button type="button" className="chatTeach" aria-expanded={mode === 'teach'} disabled={busy} onClick={() => { setEdited(content); setMode(mode === 'teach' ? null : 'teach'); }}><PenLine aria-hidden="true" />{t('feedback.teach')}</button>
    </div>
    {mode === 'reason' && <form className="chatFeedbackForm" onSubmit={(e) => void sendReason(e)}>
      <label htmlFor={`${id}-reason`} className="chatFeedbackNote">{t('feedback.reasonLabel')}</label>
      <input id={`${id}-reason`} value={reason} maxLength={500} dir="auto" onChange={(e) => setReason(e.target.value)} />
      <div className="actions"><button type="submit" disabled={busy || !reason.trim()}><Check aria-hidden="true" />{t('feedback.sendReason')}</button><button type="button" className="secondary" onClick={() => setMode(null)}>{t('feedback.skip')}</button></div>
    </form>}
    {mode === 'teach' && <form className="chatFeedbackForm" onSubmit={(e) => void teach(e)}>
      <label htmlFor={`${id}-teach`} className="chatFeedbackNote">{t('feedback.teachLabel')}</label>
      <textarea id={`${id}-teach`} value={edited} maxLength={20000} dir="auto" onChange={(e) => setEdited(e.target.value)} />
      <div className="actions"><button type="submit" disabled={busy}><Check aria-hidden="true" />{t('feedback.teachSave')}</button><button type="button" className="secondary" onClick={() => setMode(null)}>{t('common.cancel')}</button></div>
    </form>}
    {note && <p className={`chatFeedbackNote ${note.error ? 'chatError' : ''}`} role={note.error ? 'alert' : 'status'}>{note.text}</p>}
  </div>;
}
