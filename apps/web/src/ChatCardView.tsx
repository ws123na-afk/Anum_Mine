import { useState } from 'react';
import { Check, ExternalLink, FileText, Globe, Inbox, Loader2, MessageCircle, ShieldAlert, ShieldCheck, StickyNote, X, type LucideIcon } from 'lucide-react';
import { cardAfterDecision, type CardCategory, type ChatCard, type ChatCardItem } from './lib/chat';
import { decideCardApproval } from './lib/chatApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';

/** Where a card's link goes; the shell opens the matching view (docs/conversations.md#tools-in-chat). */
export type ChatTarget =
  | { kind: 'email' | 'whatsapp' | 'file'; id: string }
  | { kind: 'memory'; id: string; title: string | null }
  | { kind: 'task'; id: string }
  | { kind: 'inbox' | 'whatsappView' | 'files' | 'approvals' };

const ICONS: Record<CardCategory, LucideIcon> = { email: Inbox, whatsapp: MessageCircle, web: Globe, files: FileText, action: ShieldAlert };
const KNOWN_BADGES = new Set(['unread', 'priority_high', 'needs_reply', 'window_closed']);
const VIEW_FOR: Partial<Record<CardCategory, { target: ChatTarget; label: MessageKey }>> = {
  email: { target: { kind: 'inbox' }, label: 'chatCard.openInbox' },
  whatsapp: { target: { kind: 'whatsappView' }, label: 'chatCard.openWhatsApp' },
  files: { target: { kind: 'files' }, label: 'chatCard.openFiles' },
};

function riskLabel(level: string): string {
  return level === 'low' || level === 'medium' || level === 'high' ? t(`chatCard.approval.risk.${level}`) : level;
}

function display(value: unknown): string {
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value); } catch { return String(value); }
}

/**
 * The outcome of a chat question that ran as a task: results with links into Inbox, WhatsApp,
 * Files and Tasks; a pending approval with its payload hash and Approve/Reject; or why it failed.
 * Every value from mail, WhatsApp, the web or the model renders as a text node.
 */
export function ChatCardView({ card, live = false, onChange, onOpen }: { card: ChatCard; live?: boolean; onChange(card: ChatCard): void; onOpen?(target: ChatTarget): void }) {
  const [deciding, setDeciding] = useState<'approve' | 'reject' | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const Icon = ICONS[card.category] ?? ShieldAlert;
  const view = VIEW_FOR[card.category];
  const approval = card.approval ?? null;

  async function decide(decision: 'approve' | 'reject') {
    if (!approval || deciding) return;
    setDeciding(decision);
    setProblem(null);
    try {
      onChange(cardAfterDecision(card, await decideCardApproval(approval, decision)));
    } catch (e) {
      setProblem(isPermissionDenied(e) ? t('chatCard.approval.cannotDecide') : t('chatCard.approval.failed', { reason: describeError(e) }));
    } finally { setDeciding(null); }
  }

  const open = (target: ChatTarget) => onOpen?.(target);
  return <section className={`chatCard ${card.state}`} aria-label={t(`chatCard.title.${card.category}`)}>
    <header className="chatCardHead">
      <Icon aria-hidden="true" />
      <h3>{card.state === 'approval' ? t('chatCard.approval.title') : card.state === 'failed' ? t('chatCard.failed.title') : card.state === 'denied' ? t('chatCard.denied.title') : t(`chatCard.title.${card.category}`)}</h3>
      {card.state === 'running' && <span className="statusBadge warning" role="status"><Loader2 aria-hidden="true" className="chatCardSpin" />{t(`chatCard.running.${card.category}`)}</span>}
      {card.task_id && onOpen && <button type="button" className="secondary" onClick={() => open({ kind: 'task', id: card.task_id as string })}>{t('chatCard.openTask')}</button>}
    </header>

    {card.state === 'results' && <>
      {card.items.length > 0
        ? <ul className="chatCardItems" aria-label={t('chatCard.items')}>{card.items.map((item, index) => <li key={`${item.kind}:${item.id ?? item.url ?? index}`}><Item item={item} onOpen={onOpen ? open : undefined} /></li>)}</ul>
        : <p className="chatCardNote">{t(`chatCard.empty.${card.category}`)}</p>}
      {card.total > card.items.length && <p className="chatCardNote">{t('chatCard.more', { shown: card.items.length, total: card.total })}</p>}
      {(card.category === 'email' || card.category === 'whatsapp' || card.category === 'web') && card.items.length > 0 && <p className="chatCardNote subtle">{t('chatCard.untrusted')}</p>}
      {view && onOpen && <div className="chatCardActions"><button type="button" className="secondary" onClick={() => open(view.target)}>{t(view.label)}</button></div>}
      {approval && approval.status !== 'pending' && <p className="chatCardNote">{decidedText(approval.status)}</p>}
    </>}

    {card.state === 'approval' && approval && <>
      <p className="chatCardNote">{t('chatCard.approval.intro')}</p>
      <dl className="chatCardFacts">
        <div><dt>{t('chatCard.approval.action')}</dt><dd><code>{approval.action}</code></dd></div>
        <div><dt>{t('chatCard.approval.risk')}</dt><dd><span className={`statusBadge ${approval.risk_level === 'high' ? 'dangerTone' : 'warning'}`}>{riskLabel(approval.risk_level)}</span></dd></div>
        {approval.target && <div><dt>{t('chatCard.approval.target')}</dt><dd><code>{approval.target}</code></dd></div>}
        <div><dt>{t('chatCard.approval.hash')}</dt><dd><code className="chatCardHash">{approval.payload_hash ?? '—'}</code></dd></div>
      </dl>
      {approval.arguments && Object.keys(approval.arguments).length > 0 && <details className="chatCardArgs"><summary>{t('chatCard.approval.arguments')}</summary>
        <dl>{Object.entries(approval.arguments).map(([key, value]) => <div key={key}><dt>{key}</dt><dd dir="auto">{display(value)}</dd></div>)}</dl>
      </details>}
      {approval.expires_at && <p className="chatCardNote subtle">{t('chatCard.approval.expires', { time: formatDateTime(approval.expires_at) })}</p>}
      {approval.status !== 'pending' && <p className="chatCardNote">{decidedText(approval.status)}</p>}
      {approval.status === 'pending' && (approval.can_decide
        ? <div className="chatCardActions">
          <button type="button" className="chatCardApprove" disabled={deciding !== null || !approval.payload_hash} onClick={() => void decide('approve')}>{deciding === 'approve' ? <Loader2 aria-hidden="true" className="chatCardSpin" /> : <ShieldCheck aria-hidden="true" />}{t('chatCard.approve')}</button>
          <button type="button" className="secondary" disabled={deciding !== null} onClick={() => void decide('reject')}>{deciding === 'reject' ? <Loader2 aria-hidden="true" className="chatCardSpin" /> : <X aria-hidden="true" />}{t('chatCard.reject')}</button>
          {deciding && <span className="chatCardNote" role="status">{t('chatCard.deciding')}</span>}
        </div>
        : <div className="chatCardActions"><p className="chatCardNote">{t('chatCard.approval.cannotDecide')}</p>{onOpen && <button type="button" className="secondary" onClick={() => open({ kind: 'approvals' })}>{t('chatCard.openApprovals')}</button>}</div>)}
      {approval.status === 'approved' && <p className="chatCardNote">{t('chatCard.approval.waitingMore')}</p>}
      {problem && <p className="chatCardNote chatError" role="alert">{problem}</p>}
    </>}

    {card.state === 'running' && !live && <p className="chatCardNote">{t('chatCard.stillRunning')}</p>}
    {card.state === 'failed' && <>
      <p className="chatCardNote chatError" dir="auto">{card.summary}</p>
      {approval && <p className="chatCardNote">{decidedText(approval.status)}</p>}
    </>}
    {card.state === 'cancelled' && <p className="chatCardNote">{t('chatCard.cancelled')}</p>}
    {card.state === 'denied' && <p className="chatCardNote" dir="auto">{card.summary}</p>}
    {card.task_id && card.state !== 'running' && <p className="chatCardNote subtle"><Check aria-hidden="true" />{t('chatCard.ranAsTask')}</p>}
  </section>;
}

function decidedText(status: string): string {
  if (status === 'approved' || status === 'rejected' || status === 'expired') return t(`chatCard.approval.status.${status}`);
  return status;
}

function Item({ item, onOpen }: { item: ChatCardItem; onOpen?(target: ChatTarget): void }) {
  const title = item.title || (item.kind === 'email' ? t('chatCard.noSubject') : item.id ?? '');
  const badges = (item.badges ?? []).filter((badge) => KNOWN_BADGES.has(badge));
  const body = <>
    <span className="chatCardItemTitle" dir="auto">{title}</span>
    {(item.subtitle || item.at) && <span className="chatCardItemMeta" dir="auto">{[item.subtitle, item.at ? formatDateTime(item.at, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : null].filter(Boolean).join(' · ')}</span>}
    {item.detail && <span className="chatCardItemDetail" dir="auto">{item.detail}</span>}
    {badges.length > 0 && <span className="chatCardBadges">{badges.map((badge) => <span key={badge} className={`statusBadge ${badge === 'priority_high' || badge === 'needs_reply' ? 'warning' : 'neutral'}`}>{t(`chatCard.badge.${badge}` as MessageKey)}</span>)}</span>}
  </>;
  if (item.kind === 'web' && item.url && /^https?:\/\//i.test(item.url)) {
    return <a className="chatCardItem" href={item.url} target="_blank" rel="noopener noreferrer nofollow" aria-label={t('chatCard.openItem.web', { title })}>{body}<ExternalLink aria-hidden="true" className="chatCardItemIcon" /></a>;
  }
  if (onOpen && item.id && item.kind !== 'web') {
    const id = item.id;
    const target: ChatTarget = item.kind === 'memory' ? { kind: 'memory', id, title: item.title || null } : { kind: item.kind, id };
    const ItemIcon = item.kind === 'memory' ? StickyNote : item.kind === 'file' ? FileText : item.kind === 'whatsapp' ? MessageCircle : Inbox;
    return <button type="button" className="chatCardItem" aria-label={t(`chatCard.openItem.${item.kind}`, { title })} onClick={() => onOpen(target)}>{body}<ItemIcon aria-hidden="true" className="chatCardItemIcon" /></button>;
  }
  return <div className="chatCardItem">{body}</div>;
}
