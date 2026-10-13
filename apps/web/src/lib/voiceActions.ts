// Voice action cards (docs/voice.md#actions): the `action` event of the voice stream, checked
// before it is shown, and where the card's links go. Pure, so it is unit-tested in Node.
import type { VoiceActionCard, VoiceActionKind, VoiceActionState } from '@anum/contracts';

export type { VoiceActionCard } from '@anum/contracts';

const KINDS: ReadonlySet<string> = new Set<VoiceActionKind>(['create_task', 'assign', 'run_task', 'cancel_task', 'task_status', 'run_quick_command']);
const STATES: ReadonlySet<string> = new Set<VoiceActionState>([
  'running', 'completed', 'approval', 'failed', 'cancelled', 'created', 'status', 'not_found', 'ambiguous', 'denied', 'needs_detail',
]);

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null);
const count = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0);

/** The card from an `action` event, or null when it is not one (never breaks the conversation). */
export function asVoiceActionCard(raw: unknown): VoiceActionCard | null {
  if (!raw || typeof raw !== 'object') return null;
  const card = raw as Record<string, unknown>;
  if (typeof card.kind !== 'string' || !KINDS.has(card.kind) || typeof card.state !== 'string' || !STATES.has(card.state)) return null;
  const agent = card.agent && typeof card.agent === 'object' ? card.agent as Record<string, unknown> : null;
  const approval = card.approval && typeof card.approval === 'object' ? card.approval as VoiceActionCard['approval'] : null;
  return {
    kind: card.kind as VoiceActionKind,
    state: card.state as VoiceActionState,
    title: text(card.title),
    task_id: text(card.task_id),
    task_status: text(card.task_status) as VoiceActionCard['task_status'],
    agent: agent && text(agent.id) && text(agent.name) ? { id: agent.id as string, name: agent.name as string } : null,
    command_id: text(card.command_id),
    steps: count(card.steps),
    approvals_pending: count(card.approvals_pending),
    approval: approval && text(approval.id) ? approval : null,
    summary: typeof card.summary === 'string' ? card.summary : '',
    candidates: Array.isArray(card.candidates) ? card.candidates.filter((item): item is string => typeof item === 'string').slice(0, 5) : [],
    error_code: text(card.error_code),
  };
}

export type VoiceActionLink = { kind: 'task'; id: string } | { kind: 'approvals' };

/** Open the task when there is one; open Approvals while something waits there. */
export function actionLinks(card: VoiceActionCard): VoiceActionLink[] {
  const links: VoiceActionLink[] = [];
  if (card.task_id) links.push({ kind: 'task', id: card.task_id });
  if (card.state === 'approval' || card.approvals_pending > 0 || card.approval?.status === 'pending') links.push({ kind: 'approvals' });
  return links;
}

export type VoiceActionTone = 'success' | 'warning' | 'danger' | 'neutral';

export function actionTone(card: VoiceActionCard): VoiceActionTone {
  if (card.state === 'completed' || card.state === 'created') return 'success';
  if (card.state === 'approval' || card.state === 'running' || card.state === 'ambiguous' || card.state === 'needs_detail') return 'warning';
  if (card.state === 'failed' || card.state === 'denied' || card.state === 'not_found') return 'danger';
  return 'neutral';
}

/** The newest card wins: the stream sends one when the task exists and one with the outcome. */
export function latestCard(current: VoiceActionCard | null, raw: unknown): VoiceActionCard | null {
  return asVoiceActionCard(raw) ?? current;
}
