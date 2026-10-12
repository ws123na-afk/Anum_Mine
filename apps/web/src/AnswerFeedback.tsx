import { useId, useState, type FormEvent } from 'react';
import { Check, PenLine, ThumbsDown, ThumbsUp } from 'lucide-react';
import type { ConversationTurnFeedbackResult } from '@anum/contracts';
import { describeError } from './lib/errors';
import { t } from './lib/i18n';
import * as learningApi from './lib/learningApi';
import './learning.css';

export type Rating = 'up' | 'down';

/**
 * Feedback on one Assistant answer (docs/conversations.md#message-actions, docs/learning.md):
 * Helpful / Not helpful (with an optional reason) is stored on the answer through `onRate`
 * whatever the learning setting, and is a learning signal while learning is on; pressing
 * the same thumb again clears it. "Edit to teach" (the answer rewritten the way the user
 * would have written it) goes to learning only.
 */
export function AnswerFeedback({ turnId, content, rating, onRate }: {
  turnId: string; content: string; rating: Rating | null;
  onRate: (rating: Rating | null, reason?: string) => Promise<ConversationTurnFeedbackResult>;
}) {
  const [mode, setMode] = useState<'reason' | 'teach' | null>(null);
  const [reason, setReason] = useState(''), [edited, setEdited] = useState(content);
  const [busy, setBusy] = useState(false), [note, setNote] = useState<{ error: boolean; text: string } | null>(null);
  const id = useId();

  async function sendRating(next: Rating | null, why?: string): Promise<boolean> {
    setBusy(true); setNote(null);
    try {
      const result = await onRate(next, why);
      if (next === null) setNote({ error: false, text: t('feedback.cleared') });
      else setNote({ error: false, text: result.learning_recorded ? t('feedback.thanks') : t('feedback.savedNotLearned', { reason: result.learning_message ?? t('feedback.learningOff') }) });
      return true;
    } catch (e) { setNote({ error: true, text: t('feedback.failed', { reason: describeError(e) }) }); return false; }
    finally { setBusy(false); }
  }
  async function rate(next: Rating) {
    const value = rating === next ? null : next;
    if (await sendRating(value)) setMode(value === 'down' ? 'reason' : null);
  }
  async function sendReason(event: FormEvent) {
    event.preventDefault();
    if (reason.trim() && await sendRating('down', reason.trim())) { setMode(null); setReason(''); }
  }
  async function teach(event: FormEvent) {
    event.preventDefault();
    if (edited.trim() === content.trim()) { setNote({ error: true, text: t('feedback.unchanged') }); return; }
    setBusy(true); setNote(null);
    try {
      const result = await learningApi.sendFeedback({ surface: 'chat', subject_id: turnId, original: content, final: edited });
      setNote(result.recorded ? { error: false, text: t('feedback.teachSaved') } : { error: true, text: result.message ?? t('feedback.notRecorded') });
      if (result.recorded) setMode(null);
    } catch (e) { setNote({ error: true, text: t('feedback.failed', { reason: describeError(e) }) }); }
    finally { setBusy(false); }
  }

  return <>
    <button type="button" className="msgAction" aria-pressed={rating === 'up'} aria-label={t('feedback.helpful')} title={t('feedback.helpful')} disabled={busy} onClick={() => void rate('up')}><ThumbsUp aria-hidden="true" /></button>
    <button type="button" className="msgAction" aria-pressed={rating === 'down'} aria-label={t('feedback.notHelpful')} title={t('feedback.notHelpful')} disabled={busy} onClick={() => void rate('down')}><ThumbsDown aria-hidden="true" /></button>
    <button type="button" className="msgAction labelled" aria-expanded={mode === 'teach'} disabled={busy} onClick={() => { setEdited(content); setMode(mode === 'teach' ? null : 'teach'); }}><PenLine aria-hidden="true" /><span>{t('feedback.teach')}</span></button>
    {mode === 'reason' && <form className="chatFeedbackForm msgWide" onSubmit={(e) => void sendReason(e)}>
      <label htmlFor={`${id}-reason`} className="chatFeedbackNote">{t('feedback.reasonLabel')}</label>
      <input id={`${id}-reason`} value={reason} maxLength={500} dir="auto" onChange={(e) => setReason(e.target.value)} />
      <div className="actions"><button type="submit" disabled={busy || !reason.trim()}><Check aria-hidden="true" />{t('feedback.sendReason')}</button><button type="button" className="secondary" onClick={() => setMode(null)}>{t('feedback.skip')}</button></div>
    </form>}
    {mode === 'teach' && <form className="chatFeedbackForm msgWide" onSubmit={(e) => void teach(e)}>
      <label htmlFor={`${id}-teach`} className="chatFeedbackNote">{t('feedback.teachLabel')}</label>
      <textarea id={`${id}-teach`} value={edited} maxLength={20000} dir="auto" onChange={(e) => setEdited(e.target.value)} />
      <div className="actions"><button type="submit" disabled={busy}><Check aria-hidden="true" />{t('feedback.teachSave')}</button><button type="button" className="secondary" onClick={() => setMode(null)}>{t('common.cancel')}</button></div>
    </form>}
    {note && <p className={`chatFeedbackNote msgWide ${note.error ? 'chatError' : ''}`} role={note.error ? 'alert' : 'status'}>{note.text}</p>}
  </>;
}
