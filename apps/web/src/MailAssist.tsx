// AI on the Email Center (docs/email.md#ai-on-email): the assistant row under an open message.
// Summarise (the message or its thread), label (category and priority chips) and "Draft reply with
// AI", which writes into the person's drafts and opens the draft; sending stays the normal send that
// waits for an owner's approval. States: checking, AI not set up, denied, loading, error with retry,
// and a warning when the message tries to instruct an AI (it is content, never obeyed).
import { useEffect, useId, useState, type FormEvent } from 'react';
import { CircleDot, ListChecks, PenLine, RefreshCw, ShieldAlert, Sparkles, Tags } from 'lucide-react';
import { t } from './lib/i18n';
import type { MailAccount, MailDraft } from './lib/mail';
import { senderLabel, type MailIdentity } from './lib/mailDirectory';
import { listMailIdentities } from './lib/mailDirectoryApi';
import { identityChoice } from './lib/mailAi';
import {
  assistFailure, categoryLabel, cleanInstructions, priorityLabel, priorityTone,
  type AssistFailure, type MailLabel, type MailMessageRef, type MailSummary,
} from './lib/mailAi';
import { classifyMail, draftMailReplyWithAi, getMailAiStatus, resetMailAiStatus, summarizeMail } from './lib/mailAiApi';
import './mailAi.css';

type Busy = 'summary' | 'thread' | 'label' | 'draft' | null;
type Status = { state: 'checking' } | { state: 'ready' } | { state: 'unavailable'; reason: string } | { state: 'failed'; reason: string };

export function MailAssist({ target, account, canAct, onDrafted, onOpenApprovals }: {
  target: MailMessageRef; account: MailAccount; canAct: boolean; onDrafted: (draft: MailDraft) => void; onOpenApprovals?: () => void;
}) {
  const [status, setStatus] = useState<Status>({ state: 'checking' });
  const [busy, setBusy] = useState<Busy>(null);
  const [summary, setSummary] = useState<MailSummary | null>(null);
  const [label, setLabel] = useState<MailLabel | null>(null);
  const [failure, setFailure] = useState<{ action: Exclude<Busy, null>; failure: AssistFailure } | null>(null);
  const [composing, setComposing] = useState(false);
  const [instructions, setInstructions] = useState('');
  const [replyAll, setReplyAll] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(false);
  const titleId = useId();
  const instructionsId = useId();
  const fromId = useId();
  const [identities, setIdentities] = useState<MailIdentity[]>([]);
  // undefined: the mailbox's default identity; null: the mailbox address; else an identity id.
  const [from, setFrom] = useState<string | null | undefined>(undefined);

  const check = () => {
    setStatus({ state: 'checking' });
    getMailAiStatus().then(
      (value) => setStatus(value.available ? { state: 'ready' } : { state: 'unavailable', reason: value.reason ?? t('mailai.unavailable') }),
      (e) => setStatus({ state: 'failed', reason: assistFailure(e).text }),
    );
  };
  useEffect(check, []);
  useEffect(() => {
    if (!composing || !canAct) return;
    let live = true;
    // Senders are a choice, not a requirement: without them the draft goes out as the mailbox.
    listMailIdentities(account.id).then((found) => { if (live) setIdentities(found); }, () => { if (live) setIdentities([]); });
    return () => { live = false; };
  }, [composing, canAct, account.id]);

  async function run(action: Exclude<Busy, null>) {
    setBusy(action); setFailure(null); setNotice(null); setWaiting(false);
    try {
      if (action === 'summary' || action === 'thread') setSummary(await summarizeMail(target, action === 'thread'));
      else if (action === 'label') setLabel(await classifyMail(target));
      else {
        const result = await draftMailReplyWithAi(target, replyAll ? 'reply_all' : 'reply', cleanInstructions(instructions), identityChoice(from));
        if (result.status === 'waiting_approval') { setWaiting(true); setComposing(false); return; }
        if (result.draft) { setComposing(false); setInstructions(''); setNotice(t('mailai.drafted')); onDrafted(result.draft); }
      }
    } catch (e) {
      const found = assistFailure(e);
      if (found.kind === 'unavailable') { resetMailAiStatus(); setStatus({ state: 'unavailable', reason: found.text }); return; }
      setFailure({ action, failure: found });
    } finally { setBusy(null); }
  }

  function submitDraft(event: FormEvent) { event.preventDefault(); void run('draft'); }

  const suspected = Boolean(summary?.injection_suspected || label?.injection_suspected);
  const working = busy !== null;
  const busyText = busy === 'summary' || busy === 'thread' ? t('mailai.summarizing') : busy === 'label' ? t('mailai.classifying') : busy === 'draft' ? t('mailai.drafting') : '';

  return <section className="mailAi" aria-labelledby={titleId}>
    <div className="mailAiHead">
      <h3 id={titleId}><Sparkles aria-hidden="true" />{t('mailai.title')}</h3>
      {status.state === 'ready' && <div className="mailAiActions" role="toolbar" aria-label={t('mailai.title')}>
        <button type="button" className="secondary" disabled={working} aria-busy={busy === 'summary'} onClick={() => void run('summary')}><ListChecks aria-hidden="true" />{t('mailai.summarize')}</button>
        <button type="button" className="secondary" disabled={working} aria-busy={busy === 'thread'} onClick={() => void run('thread')}>{t('mailai.summarizeThread')}</button>
        <button type="button" className="secondary" disabled={working} aria-busy={busy === 'label'} onClick={() => void run('label')}><Tags aria-hidden="true" />{t('mailai.classify')}</button>
        {canAct && <button type="button" className="secondary" disabled={working} aria-expanded={composing} onClick={() => setComposing(!composing)}><PenLine aria-hidden="true" />{t('mailai.draft')}</button>}
      </div>}
      {label && <ul className="mailAiChips" aria-label={t('mailai.labelsTitle')}>
        <li><span className="statusBadge neutral">{categoryLabel(label.category)}</span></li>
        <li><span className={`statusBadge ${priorityTone(label.priority)}`}>{priorityLabel(label.priority)}</span></li>
      </ul>}
    </div>

    {status.state === 'checking' && <p className="muted mailAiNote" role="status">{t('mailai.checking')}</p>}
    {status.state === 'unavailable' && <div className="mailAiNote" role="status"><p><strong>{status.reason}</strong></p><p className="muted">{t('mailai.unavailableHint')}</p></div>}
    {status.state === 'failed' && <div className="mailAiNote">
      <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('mailai.statusFailed', { reason: status.reason })}</p>
      <button type="button" className="secondary" onClick={check}><RefreshCw aria-hidden="true" />{t('mailai.retry')}</button>
    </div>}

    {status.state === 'ready' && <>
      {!summary && !label && !failure && !working && !notice && !waiting && <p className="muted mailSmall">{t('mailai.hint')}</p>}

      {composing && canAct && <form className="mailAiDraft" onSubmit={submitDraft}>
        {identities.length > 0 && <label className="mailAiFrom" htmlFor={fromId}><span>{t('mail.composer.from')}</span>
          <select id={fromId} dir="ltr" value={from === undefined ? (identities.find((i) => i.is_default)?.id ?? '') : from ?? ''} onChange={(e) => setFrom(e.target.value || null)}>
            <option value="">{t('mails.from.mailbox', { sender: senderLabel(account.display_name, account.address) })}</option>
            {identities.map((i) => <option key={i.id} value={i.id}>{senderLabel(i.display_name ?? account.display_name, i.address)}</option>)}
          </select>
        </label>}
        <label htmlFor={instructionsId}>{t('mailai.instructions')}</label>
        <textarea id={instructionsId} rows={2} maxLength={1000} value={instructions} aria-describedby={`${instructionsId}-hint`} onChange={(e) => setInstructions(e.target.value)} dir="auto" />
        <p id={`${instructionsId}-hint`} className="muted mailSmall">{t('mailai.instructionsHint')}</p>
        <div className="actions">
          <label className="mailAiCheck"><input type="checkbox" checked={replyAll} onChange={(e) => setReplyAll(e.target.checked)} />{t('mailai.draftAll')}</label>
          <button type="submit" disabled={working} aria-busy={busy === 'draft'}><PenLine aria-hidden="true" />{t('mailai.writeDraft')}</button>
          <button type="button" className="secondary" disabled={working} onClick={() => setComposing(false)}>{t('mailai.cancel')}</button>
        </div>
      </form>}

      {working && <p className="muted mailAiNote" role="status" aria-live="polite">{busyText}</p>}
      {failure && <div className="mailAiNote">
        <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{failure.failure.text}</p>
        {failure.failure.kind === 'error' && <button type="button" className="secondary" onClick={() => void run(failure.action)}><RefreshCw aria-hidden="true" />{t('mailai.retry')}</button>}
      </div>}
      {notice && <p className="notice mailAiNote" role="status"><CircleDot aria-hidden="true" />{notice}</p>}
      {waiting && <p className="notice mailAiNote" role="status"><CircleDot aria-hidden="true" />{t('mailai.draftWaiting')}{onOpenApprovals && <> <button type="button" className="linkButton" onClick={onOpenApprovals}>{t('mailai.openApprovals')}</button></>}</p>}
      {suspected && <p className="notice mailAiWarn" role="status"><ShieldAlert aria-hidden="true" />{t('mailai.injection')}</p>}
      {label?.source === 'rules' && <p className="muted mailSmall">{t('mailai.labelSource.rules')}</p>}
      {label?.reason && <p className="muted mailSmall" dir="auto">{label.reason}</p>}

      {summary && <div className="mailAiSummary" role="region" aria-label={t('mailai.summaryTitle')}>
        <h4>{t('mailai.summaryTitle')} <small className="muted">{t('mailai.threadOf', { count: summary.messages })}</small></h4>
        <p dir="auto">{summary.summary}</p>
        {summary.key_points.length > 0 && <><h5>{t('mailai.keyPoints')}</h5><ul>{summary.key_points.map((point, i) => <li key={i} dir="auto">{point}</li>)}</ul></>}
        <h5>{t('mailai.actionItems')}</h5>
        {summary.action_items.length > 0 ? <ul>{summary.action_items.map((item, i) => <li key={i} dir="auto">{item}</li>)}</ul> : <p className="muted">{t('mailai.noActions')}</p>}
        <p className="muted mailSmall">{t('mailai.byAi', { model: summary.model })}</p>
      </div>}
    </>}
  </section>;
}
