// Pure helpers for the Assistant chat and streamed voice replies (docs/conversations.md):
// an incremental server-sent-events parser, the chat transcript reducer, sentence
// splitting for speaking a reply while it streams, and the auto-scroll rule.
// No browser or Vite APIs, so `pnpm test:unit` exercises them directly.
import type { ReportCard } from '@anum/contracts';
import { t as msg } from './i18n.ts';

export type TurnStatus = 'complete' | 'cancelled' | 'failed';

/** A question run as a task (docs/conversations.md#tools-in-chat): its results, approval or failure. */
export type CardState = 'running' | 'results' | 'approval' | 'failed' | 'cancelled' | 'denied';
export type CardCategory = 'email' | 'whatsapp' | 'commerce' | 'tyre' | 'web' | 'files' | 'action';

export interface ChatCardItem {
  kind: 'email' | 'whatsapp' | 'web' | 'file' | 'memory' | 'order' | 'listing' | 'tyre';
  id?: string | null;
  title: string;
  subtitle?: string | null;
  detail?: string | null;
  at?: string | null;
  badges?: string[];
  url?: string | null;
}

export interface ChatCardApproval {
  id: string;
  action: string;
  risk_level: string;
  status: string;
  reason: string;
  payload_hash?: string | null;
  arguments?: Record<string, unknown>;
  target?: string | null;
  expires_at?: string | null;
  can_decide: boolean;
}

export interface ChatCard {
  state: CardState;
  category: CardCategory;
  task_id?: string | null;
  run_id?: string | null;
  tool?: string | null;
  summary: string;
  items: ChatCardItem[];
  total: number;
  approval?: ChatCardApproval | null;
  error_code?: string | null;
}

export interface ChatTurn {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant';
  content: string;
  status: TurnStatus;
  created_at: string;
  card?: ChatCard | null;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  /** A thread with an agent (docs/agents.md); absent or null for the Assistant's own. */
  agent_id?: string | null;
}

export interface ConversationDetail extends Conversation {
  turns: ChatTurn[];
  /** Cards for the Assistant's structured answers, by assistant turn (docs/reports.md). */
  reports?: ReportCard[];
}

export interface SseEvent {
  event: string;
  data: unknown;
}

/**
 * Feed decoded text in any chunking; complete events are delivered in order. Frames end
 * with a blank line; `data:` lines are joined; comments (`:`) are ignored; data that is
 * not JSON is passed on as a string.
 */
export function createSseParser(onEvent: (event: SseEvent) => void): { push(text: string): void; end(): void } {
  let buffer = '';
  const emit = (frame: string) => {
    let event = 'message';
    const data: string[] = [];
    for (const raw of frame.split('\n')) {
      if (!raw || raw.startsWith(':')) continue;
      const colon = raw.indexOf(':');
      const field = colon < 0 ? raw : raw.slice(0, colon);
      const value = colon < 0 ? '' : raw.slice(colon + 1).replace(/^ /, '');
      if (field === 'event') event = value;
      else if (field === 'data') data.push(value);
    }
    if (!data.length) return;
    const text = data.join('\n');
    let parsed: unknown = text;
    try { parsed = JSON.parse(text); } catch { /* plain text data */ }
    onEvent({ event, data: parsed });
  };
  return {
    push(text: string) {
      buffer += text.replace(/\r\n?/g, '\n');
      let index = buffer.indexOf('\n\n');
      while (index >= 0) {
        emit(buffer.slice(0, index));
        buffer = buffer.slice(index + 2);
        index = buffer.indexOf('\n\n');
      }
    },
    end() {
      if (buffer.trim()) emit(buffer);
      buffer = '';
    },
  };
}

/** A turn as the chat shows it: stored turns plus the question being sent and the reply being streamed. */
export interface UiTurn {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  status: TurnStatus | 'sending' | 'streaming';
  error?: string;
  errorCode?: string;
  /** A structured answer's report card ("View Full Report"). */
  report?: ReportCard;
  card?: ChatCard | null;
}

export const PENDING_USER = 'pending-user';
export const PENDING_ASSISTANT = 'pending-assistant';
let localTurns = 0;

export function fromStored(turns: ChatTurn[], reports: ReportCard[] = []): UiTurn[] {
  const cards = new Map(reports.filter((r) => r.turn_id).map((r) => [r.turn_id as string, r]));
  return turns.map((t) => {
    const report = cards.get(t.id);
    const card = asCard(t.card);
    return report ? { id: t.id, role: t.role, content: t.content, status: t.status, card, report } : { id: t.id, role: t.role, content: t.content, status: t.status, card };
  });
}

function asReportCard(value: unknown): ReportCard | undefined {
  return value && typeof value === 'object' && typeof (value as ReportCard).report_id === 'string' && Array.isArray((value as ReportCard).metrics) ? value as ReportCard : undefined;
}

/** A card the client can show, or null (missing, or a shape this client cannot read). */
export function asCard(value: unknown): ChatCard | null {
  if (!value || typeof value !== 'object') return null;
  const card = value as ChatCard;
  if (typeof card.state !== 'string' || typeof card.category !== 'string') return null;
  return { ...card, summary: typeof card.summary === 'string' ? card.summary : '', items: Array.isArray(card.items) ? card.items : [], total: typeof card.total === 'number' ? card.total : 0 };
}

/** A stored card after an Approve or Reject: the decision's task, run and approval. */
export function cardAfterDecision(card: ChatCard, decision: { task: { status: string }; run?: { result?: string | null; steps?: { type: string; summary: string }[] } | null; approval: { status: string } }): ChatCard {
  const approval = card.approval ? { ...card.approval, status: decision.approval.status } : card.approval;
  const status = decision.task.status;
  if (status === 'completed') return { ...card, approval, state: 'results', summary: decision.run?.result ?? card.summary };
  if (status === 'failed') {
    const steps = decision.run?.steps ?? [];
    const reason = [...steps].reverse().find((step) => step.type === 'tool_result')?.summary;
    return { ...card, approval, state: 'failed', summary: reason ?? decision.run?.result ?? card.summary };
  }
  if (status === 'cancelled') return { ...card, approval, state: 'cancelled' };
  if (status === 'waiting_approval') return { ...card, approval, state: 'approval' };
  return { ...card, approval, state: 'running' };
}

/** Show the question at once and an empty reply that fills as it streams. A retry reuses the stored question. */
export function startAsk(turns: UiTurn[], question: string | null): UiTurn[] {
  const next = turns.filter((t) => t.status !== 'streaming' && t.status !== 'sending');
  if (question !== null) next.push({ id: PENDING_USER, role: 'user', content: question, status: 'sending' });
  next.push({ id: PENDING_ASSISTANT, role: 'assistant', content: '', status: 'streaming' });
  return next;
}

function asTurn(value: unknown): ChatTurn | null {
  return value && typeof value === 'object' && typeof (value as ChatTurn).id === 'string' ? value as ChatTurn : null;
}

/** Apply one event of `POST /conversations/{id}/ask` to the transcript. */
export function applyAskEvent(turns: UiTurn[], event: SseEvent): UiTurn[] {
  const data = (event.data && typeof event.data === 'object' ? event.data : {}) as Record<string, unknown>;
  if (event.event === 'turn') {
    const user = asTurn(data.user_turn);
    if (!user) return turns;
    return turns.map((t) => t.id === PENDING_USER ? { id: user.id, role: 'user', content: user.content, status: user.status } : t);
  }
  if (event.event === 'tool') {
    const card = asCard(data.card);
    if (!card) return turns;
    return turns.map((t) => t.id === PENDING_ASSISTANT ? { ...t, card } : t);
  }
  if (event.event === 'delta' && typeof data.text === 'string') {
    const text = data.text;
    return turns.map((t) => t.id === PENDING_ASSISTANT ? { ...t, content: t.content + text } : t);
  }
  if (event.event === 'done') {
    const reply = asTurn(data.assistant_turn);
    const report = asReportCard(data.report);
    return turns.map((t) => t.id === PENDING_ASSISTANT
      ? reply ? { id: reply.id, role: 'assistant', content: reply.content, status: reply.status, card: asCard(reply.card) ?? t.card ?? null, ...(report ? { report } : {}) } : { ...t, id: `local-${++localTurns}`, status: 'complete' }
      : t);
  }
  if (event.event === 'error') {
    const reply = asTurn(data.assistant_turn);
    const message = typeof data.message === 'string' ? data.message : msg('chat.modelNoAnswer');
    const code = typeof data.code === 'string' ? data.code : undefined;
    return turns.map((t) => t.id === PENDING_ASSISTANT
      ? { id: reply?.id ?? `local-${++localTurns}`, role: 'assistant', content: reply?.content ?? t.content, status: 'failed', error: message, errorCode: code, card: t.card ?? null }
      : t);
  }
  return turns;
}

/** The request ended without `done` or `error`: Stop, a dropped connection, or an HTTP error. */
export function endAsk(turns: UiTurn[], outcome: { stopped: boolean; error?: string; errorCode?: string }): UiTurn[] {
  // Settled placeholders get their own ids, so the next ask's placeholders never collide with them.
  const settle = (t: UiTurn): UiTurn => t.id === PENDING_USER || t.id === PENDING_ASSISTANT ? { ...t, id: `local-${++localTurns}` } : t;
  return turns
    // A question the server never stored (the request failed before `turn`) stays visible but marked failed.
    .map((t): UiTurn => t.id === PENDING_USER ? settle({ ...t, status: 'failed' }) : t)
    .map((t): UiTurn => t.status === 'streaming' ? settle(t) : t)
    .map((t): UiTurn => t.status === 'streaming'
      ? outcome.stopped
        ? { ...t, status: 'cancelled' }
        : { ...t, status: 'failed', error: outcome.error ?? msg('chat.connectionDropped'), errorCode: outcome.errorCode }
      : t);
}

/** The latest stored question, which Retry asks again; null when there is none or it was never stored. */
export function retryTarget(turns: UiTurn[]): UiTurn | null {
  for (let i = turns.length - 1; i >= 0; i--) {
    const turn = turns[i];
    if (turn.role !== 'user') continue;
    return turn.id === PENDING_USER || turn.status === 'failed' ? null : turn;
  }
  return null;
}

/** Retry is offered when the last reply failed or was stopped. */
export function canRetry(turns: UiTurn[]): boolean {
  const last = turns[turns.length - 1];
  return Boolean(last && last.role === 'assistant' && (last.status === 'failed' || last.status === 'cancelled') && retryTarget(turns));
}

const SENTENCE_END = /([.!?。؟…]+["'”’)\]]*)(\s+)|(\n+)/g;

/**
 * Split off the complete sentences of a growing reply so speech can start before the
 * reply ends. The unfinished tail is returned as `rest`; pass `final` at the end to flush it.
 * Very short fragments ("e.g.", "1.") are merged into the next sentence.
 */
export function takeSentences(text: string, final = false, minLength = 12): { sentences: string[]; rest: string } {
  const sentences: string[] = [];
  let start = 0;
  let pending = '';
  for (const match of text.matchAll(SENTENCE_END)) {
    const end = (match.index ?? 0) + (match[1]?.length ?? 0);
    const piece = (pending + text.slice(start, end)).trim();
    start = (match.index ?? 0) + match[0].length;
    if (piece.length < minLength) { pending = piece ? `${piece} ` : ''; continue; }
    sentences.push(piece);
    pending = '';
  }
  let rest = pending + text.slice(start);
  if (final) {
    if (rest.trim()) sentences.push(rest.trim());
    rest = '';
  }
  return { sentences, rest };
}

/** Follow the stream only while the reader is at (or near) the bottom; scrolling up to read stops it. */
export function isNearBottom(box: { scrollTop: number; scrollHeight: number; clientHeight: number }, threshold = 48): boolean {
  return box.scrollHeight - box.scrollTop - box.clientHeight <= threshold;
}

export function conversationTitle(conversation: Conversation): string {
  return conversation.title.trim() || msg('chat.newConversation');
}
