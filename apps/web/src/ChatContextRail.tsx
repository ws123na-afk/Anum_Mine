// The AI Chat context rail (docs/conversations.md#context-rail): what the open conversation used and
// produced. Everything comes from the API: the conversation's own turns (task cards carry the records
// an answer used, the task it ran as and any approval it asked for) and the memories the Assistant
// remembered from this chat (GET /api/v1/memories?source_type=conversation).
import { Brain, CircleDot, ExternalLink, FileText, Inbox, ListChecks, MessageCircle, Search, ShieldCheck } from 'lucide-react';
import type { ChatTarget } from './ChatCardView';
import type { MemoryNote } from './lib/api';
import { chatContext, type ContextSource } from './lib/pageSummary';
import type { UiTurn } from './lib/chat';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';

export type ChatMemories = { state: 'loading' } | { state: 'ready'; items: MemoryNote[] } | { state: 'error'; retry: () => void };

const SOURCE_ICON: Record<string, typeof FileText> = { file: FileText, memory: Brain, email: Inbox, whatsapp: MessageCircle };
const SOURCE_KINDS = new Set(['email', 'whatsapp', 'web', 'file', 'memory', 'order', 'listing', 'tyre']);
const APPROVAL_STATES = new Set(['pending', 'approved', 'rejected', 'expired', 'cancelled']);

function sourceTarget(source: ContextSource): ChatTarget | null {
  if (!source.id) return null;
  if (source.kind === 'file' || source.kind === 'email' || source.kind === 'whatsapp') return { kind: source.kind, id: source.id };
  if (source.kind === 'memory') return { kind: 'memory', id: source.id, title: source.title };
  if (source.kind === 'order' || source.kind === 'listing') return { kind: 'commerce' };
  if (source.kind === 'tyre') return { kind: 'tyre' };
  return null;
}

export function ChatContextRail({ conversation, turns, memories, agentName, onOpen }: {
  conversation: { id: string; created_at: string; updated_at: string; pinned?: boolean } | null;
  turns: UiTurn[]; memories: ChatMemories; agentName: string | null; onOpen?: (target: ChatTarget) => void;
}) {
  const context = chatContext(turns);
  const saved = memories.state === 'ready' ? memories.items : [];
  return <div className="chatRail">
    <section className="railSection" aria-labelledby="chat-rail-about">
      <h3 id="chat-rail-about">{t('pages.chat.rail.about')}</h3>
      {conversation ? <dl className="railFacts">
        <dt>{t('pages.chat.rail.with')}</dt><dd dir="auto">{agentName ?? t('pages.chat.assistant')}</dd>
        <dt>{t('pages.chat.rail.messages')}</dt><dd>{t('pages.chat.rail.messageCount', { questions: formatNumber(context.questions), answers: formatNumber(context.answers) })}</dd>
        <dt>{t('pages.chat.rail.started')}</dt><dd><time dateTime={conversation.created_at}>{formatDateTime(conversation.created_at, { dateStyle: 'medium', timeStyle: 'short' })}</time></dd>
        <dt>{t('pages.chat.rail.lastActive')}</dt><dd><time dateTime={conversation.updated_at}>{formatDateTime(conversation.updated_at, { dateStyle: 'medium', timeStyle: 'short' })}</time></dd>
      </dl> : <p>{t('pages.chat.rail.new')}</p>}
    </section>

    <section className="railSection" aria-labelledby="chat-rail-sources">
      <h3 id="chat-rail-sources"><Search aria-hidden="true" />{t('pages.chat.rail.sources')}</h3>
      {context.sources.length === 0 ? <p>{t('pages.chat.rail.noSources')}</p> : <ul className="railList">{context.sources.map((source, index) => {
        const target = sourceTarget(source);
        const Icon = SOURCE_ICON[source.kind] ?? CircleDot;
        const body = <><strong dir="auto"><Icon aria-hidden="true" className="railIcon" />{source.title}</strong><small>{SOURCE_KINDS.has(source.kind) ? t(`pages.chat.kind.${source.kind}` as MessageKey) : source.kind}{source.subtitle ? ` · ${source.subtitle}` : ''}</small></>;
        return <li key={`${source.kind}-${source.id ?? index}`}>
          {target && onOpen ? <button type="button" className="railItem" onClick={() => onOpen(target)}>{body}</button>
            : source.url && /^https?:\/\//.test(source.url) ? <a className="railItem" href={source.url} target="_blank" rel="noopener noreferrer">{body}<small><ExternalLink aria-hidden="true" className="railIcon" />{t('pages.chat.rail.openSite')}</small></a>
              : <div className="railItem">{body}</div>}
        </li>;
      })}</ul>}
    </section>

    <section className="railSection" aria-labelledby="chat-rail-memories">
      <h3 id="chat-rail-memories"><Brain aria-hidden="true" />{t('pages.chat.rail.memories')}</h3>
      {memories.state === 'loading' && <p>{t('common.loading')}</p>}
      {memories.state === 'error' && <p>{t('pages.chat.rail.memoriesFailed')} <button type="button" className="linkButton" onClick={memories.retry}>{t('pages.chat.rail.retry')}</button></p>}
      {memories.state === 'ready' && saved.length === 0 && <p>{conversation ? t('pages.chat.rail.noMemories') : t('pages.chat.rail.memoriesLater')}</p>}
      {saved.length > 0 && <ul className="railList">{saved.map((memory) => <li key={memory.id}>
        {onOpen ? <button type="button" className="railItem" onClick={() => onOpen({ kind: 'memory', id: memory.id, title: memory.content.slice(0, 80) })}><strong dir="auto">{memory.content}</strong><small>{formatDateTime(memory.created_at, { dateStyle: 'medium' })}</small></button>
          : <div className="railItem"><strong dir="auto">{memory.content}</strong></div>}
      </li>)}</ul>}
    </section>

    <section className="railSection" aria-labelledby="chat-rail-work">
      <h3 id="chat-rail-work"><ListChecks aria-hidden="true" />{t('pages.chat.rail.work')}</h3>
      {context.tasks.length === 0 && context.approvals.length === 0 && <p>{t('pages.chat.rail.noWork')}</p>}
      {context.tasks.length > 0 && <ul className="railList">{context.tasks.map((task) => <li key={task.taskId}>
        <button type="button" className="railItem" disabled={!onOpen} onClick={() => onOpen?.({ kind: 'task', id: task.taskId })}><strong dir="auto">{task.summary || task.taskId}</strong><small>{t(`pages.chat.taskState.${task.state}` as MessageKey)}</small></button>
      </li>)}</ul>}
      {context.approvals.length > 0 && <ul className="railList">{context.approvals.map((approval) => <li key={approval.id}>
        <button type="button" className="railItem" disabled={!onOpen} onClick={() => onOpen?.({ kind: 'approvals' })}><strong dir="auto"><ShieldCheck aria-hidden="true" className="railIcon" />{approval.summary || approval.action}</strong><small><span className={`statusBadge ${approval.status === 'pending' ? 'warning' : approval.status === 'approved' ? 'success' : 'neutral'}`}>{APPROVAL_STATES.has(approval.status) ? t(`status.${approval.status}` as MessageKey) : approval.status}</span> {t('pages.chat.rail.risk', { risk: approval.risk })}</small></button>
      </li>)}</ul>}
    </section>
  </div>;
}
