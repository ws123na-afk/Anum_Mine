// Share a report by email or WhatsApp (docs/reports.md#sharing). Nothing is sent from here:
// each request becomes a governed send waiting for an owner's approval with exactly this text.
import { useEffect, useState, type FormEvent } from 'react';
import { CircleDot, Mail, MessageCircle, Send } from 'lucide-react';
import type { Report, ReportShareResult } from '@anum/contracts';
import type { EmailAccount } from './lib/email';
import { listEmailAccounts } from './lib/emailApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { t } from './lib/i18n';
import * as lib from './lib/libraryApi';
import type { WaConversation } from './lib/whatsapp';
import { listConversations } from './lib/whatsappApi';

type Channel = 'email' | 'whatsapp';

export function ReportShare({ report, onOpenApprovals, onOpenInbox }: { report: Report; onOpenApprovals?: () => void; onOpenInbox?: () => void }) {
  const [channel, setChannel] = useState<Channel>('email');
  const [accounts, setAccounts] = useState<EmailAccount[] | null>(null);
  const [conversations, setConversations] = useState<WaConversation[] | null>(null);
  const [accountId, setAccountId] = useState('');
  const [to, setTo] = useState('');
  const [conversationId, setConversationId] = useState('');
  const [note, setNote] = useState('');
  const [table, setTable] = useState(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ReportShareResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (channel === 'email' && accounts === null) listEmailAccounts().then((rows) => { const usable = rows.filter((a) => a.status === 'connected'); setAccounts(usable); setAccountId(usable[0]?.id ?? ''); }).catch(() => setAccounts([]));
    if (channel === 'whatsapp' && conversations === null) listConversations({ limit: 50, filter: 'all' }).then((rows) => { const open = rows.filter((c) => c.window_open); setConversations(open); setConversationId(open[0]?.id ?? ''); }).catch(() => setConversations([]));
  }, [channel, accounts, conversations]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setProblem(null); setResult(null);
    const recipients = to.split(',').map((value) => value.trim()).filter(Boolean);
    if (channel === 'email' && (!accountId || !recipients.length)) { setProblem(t('reports.share.needRecipient')); return; }
    if (channel === 'whatsapp' && !conversationId) { setProblem(t('reports.share.needConversation')); return; }
    setBusy(true);
    try {
      setResult(channel === 'email'
        ? await lib.shareReportByEmail(report.id, { account_id: accountId, to: recipients, note: note.trim(), include_table: table })
        : await lib.shareReportByWhatsApp(report.id, { conversation_id: conversationId, note: note.trim() }));
    } catch (e) {
      setProblem(isPermissionDenied(e) ? t(channel === 'email' ? 'reports.share.emailDenied' : 'reports.share.whatsappDenied') : describeError(e));
    } finally { setBusy(false); }
  }

  const options = channel === 'email' ? accounts : conversations;
  return <section className="reportManage" aria-labelledby="report-share">
    <h3 id="report-share">{t('reports.share.title')}</h3>
    <p className="muted">{t('reports.share.intro')}</p>
    <div className="libraryChips" role="tablist" aria-label={t('reports.share.title')}>
      {(['email', 'whatsapp'] as const).map((value) => <button type="button" role="tab" key={value} aria-selected={channel === value} className={`libChip ${channel === value ? 'active' : ''}`} onClick={() => { setChannel(value); setResult(null); setProblem(null); }}>
        {value === 'email' ? <Mail aria-hidden="true" /> : <MessageCircle aria-hidden="true" />}{t(`reports.share.${value}`)}
      </button>)}
    </div>
    {options === null && <p className="muted" role="status">{t('common.loading')}</p>}
    {options !== null && options.length === 0 && <div className="libraryState">
      <p>{channel === 'email' ? t('reports.share.noMailbox') : t('reports.share.noConversation')}</p>
      {channel === 'email' && onOpenInbox && <button type="button" className="secondary" onClick={onOpenInbox}>{t('reports.share.openInbox')}</button>}
    </div>}
    {options !== null && options.length > 0 && <form className="reportRename" onSubmit={submit}>
      {channel === 'email' ? <>
        <label className="field"><span>{t('reports.share.from')}</span>
          <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>{(accounts ?? []).map((a) => <option key={a.id} value={a.id}>{a.address}</option>)}</select>
        </label>
        <label className="field"><span>{t('reports.share.to')}</span><input value={to} onChange={(e) => setTo(e.target.value)} placeholder="name@example.com" dir="ltr" autoComplete="off" /></label>
        <label className="kbVisibility"><span><input type="checkbox" checked={table} onChange={(e) => setTable(e.target.checked)} /> {t('reports.share.includeTable')}</span></label>
      </> : <label className="field"><span>{t('reports.share.conversation')}</span>
        <select value={conversationId} onChange={(e) => setConversationId(e.target.value)}>{(conversations ?? []).map((c) => <option key={c.id} value={c.id}>{c.contact_name ?? `+${c.wa_id}`}</option>)}</select>
      </label>}
      <label className="field"><span>{t('reports.share.note')}</span><textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={channel === 'email' ? 2000 : 500} rows={3} dir="auto" /></label>
      {problem && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{problem}</p>}
      {result && <p className="notice" role="status"><CircleDot aria-hidden="true" />{result.status === 'awaiting_approval' ? t('reports.share.waiting') : result.detail}
        {result.status === 'awaiting_approval' && onOpenApprovals && <button type="button" className="linkButton" onClick={onOpenApprovals}>{t('reports.share.openApprovals')}</button>}</p>}
      <div className="actions"><button type="submit" disabled={busy}><Send aria-hidden="true" />{busy ? t('reports.share.asking') : t('reports.share.ask')}</button></div>
    </form>}
  </section>;
}
