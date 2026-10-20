// "Create ticket" from an inbox (docs/business.md#tickets-from-an-inbox): a short form that opens a
// Business support ticket linked to one email message or one WhatsApp conversation. The ticket keeps
// only the link and who wrote in; the inbox stays the record of what was said.
import { useId, useState, type FormEvent } from 'react';
import { CircleDot, LifeBuoy } from 'lucide-react';
import type { TicketDetail, TicketSeverity } from '@anum/contracts';
import { createTicketFromMessage } from './lib/businessApi';
import { SEVERITIES, openTicketFromConflict, severityLabel, ticketFromMessageBody, ticketHref, type MessageSource } from './lib/businessSupport';
import { ApiError, describeError } from './lib/errors';
import { t } from './lib/i18n';
import { Modal } from './Dialog';
import './ticketFromMessage.css';

type Problem = { text: string; openTicket: { reference: string; id: string } | null };

export function CreateTicketDialog({ source, refId, defaultTitle, onClose, onCreated }: {
  source: MessageSource; refId: string; defaultTitle: string; onClose: () => void; onCreated: (ticket: TicketDetail) => void;
}) {
  const [title, setTitle] = useState(defaultTitle);
  const [severity, setSeverity] = useState<TicketSeverity>('normal');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const ids = { title: useId(), severity: useId(), hint: useId() };

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = ticketFromMessageBody(source, refId, title, severity);
    if (!body.ok) { setProblem({ text: body.error, openTicket: null }); return; }
    setBusy(true); setProblem(null);
    try {
      onCreated(await createTicketFromMessage(body.value));
    } catch (e) {
      const text = describeError(e);
      const openTicket = e instanceof ApiError && e.status === 409 ? openTicketFromConflict(e.detail ?? text) : null;
      setProblem({ text, openTicket });
      setBusy(false);
    }
  }

  return <Modal title={t('tfm.title')} className="ticketFromMessage" onClose={busy ? () => undefined : onClose}>
    <form className="ticketFromMessageForm" onSubmit={(e) => void submit(e)} aria-busy={busy}>
      <p className="muted" id={ids.hint}>{source === 'email' ? t('tfm.introEmail') : t('tfm.introWhatsApp')}</p>
      <label className="field" htmlFor={ids.title}><span>{t('tfm.titleLabel')}</span>
        <input id={ids.title} dir="auto" value={title} maxLength={200} placeholder={source === 'whatsapp' ? t('tfm.titlePlaceholderWhatsApp') : t('tfm.titlePlaceholderEmail')} aria-describedby={ids.hint} onChange={(e) => setTitle(e.target.value)} data-autofocus />
      </label>
      <label className="field" htmlFor={ids.severity}><span>{t('biz.sup.field.severity')}</span>
        <select id={ids.severity} value={severity} onChange={(e) => setSeverity(e.target.value as TicketSeverity)}>
          {SEVERITIES.map((value) => <option key={value} value={value}>{severityLabel(value)}</option>)}
        </select>
      </label>
      {problem && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{problem.text}{problem.openTicket && <> <a href={ticketHref(problem.openTicket.id)}>{t('tfm.openExisting', { reference: problem.openTicket.reference })}</a></>}</span></p>}
      <div className="actions">
        <button type="submit" disabled={busy}><LifeBuoy aria-hidden="true" />{busy ? t('tfm.creating') : t('tfm.create')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={onClose}>{t('common.cancel')}</button>
      </div>
    </form>
  </Modal>;
}

/** The status line after a ticket was opened: its reference and a link to it. */
export function TicketCreatedNotice({ ticket }: { ticket: Pick<TicketDetail, 'id' | 'reference'> }) {
  return <p className="notice" role="status" aria-live="polite"><CircleDot aria-hidden="true" /><span>{t('tfm.created', { reference: ticket.reference })} <a href={ticketHref(ticket.id)}>{t('tfm.openTicket')}</a></span></p>;
}
