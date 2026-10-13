import { Bot, CheckCircle2, CircleAlert, ListChecks, Loader2, ShieldAlert, type LucideIcon } from 'lucide-react';
import { actionLinks, actionTone, type VoiceActionCard } from './lib/voiceActions';
import { t } from './lib/i18n';
import './voiceActions.css';

const TONE_BADGE = { success: 'success', warning: 'warning', danger: 'dangerTone', neutral: 'neutral' } as const;
const STATE_ICON: Partial<Record<VoiceActionCard['state'], LucideIcon>> = {
  completed: CheckCircle2, created: CheckCircle2, approval: ShieldAlert, failed: CircleAlert, denied: CircleAlert, not_found: CircleAlert,
};

/**
 * What a spoken request for work did (docs/voice.md#actions): the task and its agent, where it
 * stands, and links to the task and to Approvals. Approvals are decided there, never here.
 * Every value from the server or the model renders as a text node.
 */
export function VoiceActionCardView({ card, assistantName, onOpenTask, onOpenApprovals }: {
  card: VoiceActionCard;
  assistantName: string;
  onOpenTask?: (taskId: string) => void;
  onOpenApprovals?: () => void;
}) {
  const tone = actionTone(card);
  const Icon = card.state === 'running' ? Loader2 : STATE_ICON[card.state] ?? (card.kind === 'run_quick_command' ? ListChecks : Bot);
  const links = actionLinks(card);
  return <section className={`voiceActionCard ${tone}`} aria-label={t('voiceAction.label', { name: assistantName })}>
    <header className="voiceActionHead">
      <Icon aria-hidden="true" className={card.state === 'running' ? 'voiceActionSpin' : undefined} />
      <h3>{t(`voiceAction.kind.${card.kind}`)}</h3>
      <span className={`statusBadge ${TONE_BADGE[tone]}`} role={card.state === 'running' ? 'status' : undefined}>{t(`voiceAction.state.${card.state}`)}</span>
    </header>
    {card.title && <p className="voiceActionTitle" dir="auto">{card.title}</p>}
    {(card.agent || card.steps > 0 || card.approvals_pending > 0) && <p className="voiceActionFacts">
      {card.agent && <span dir="auto">{t('voiceAction.agent', { name: card.agent.name })}</span>}
      {card.steps > 0 && <span>{t('voiceAction.steps', { count: card.steps })}</span>}
      {card.approvals_pending > 0 && <span>{t('voiceAction.approvalsPending', { count: card.approvals_pending })}</span>}
    </p>}
    {card.candidates.length > 0 && <ul className="voiceActionCandidates" aria-label={t('voiceAction.candidates')}>
      {card.candidates.map((name) => <li key={name} dir="auto">{name}</li>)}
    </ul>}
    {card.state === 'approval' && <p className="voiceActionNote">{t('voiceAction.approvalNote')}</p>}
    {links.length > 0 && <div className="voiceActionLinks">
      {links.map((link) => link.kind === 'task'
        ? onOpenTask && <button key="task" type="button" className="secondary" onClick={() => onOpenTask(link.id)}>{t('voiceAction.openTask')}</button>
        : onOpenApprovals && <button key="approvals" type="button" onClick={onOpenApprovals}><ShieldAlert aria-hidden="true" />{t('voiceAction.openApprovals')}</button>)}
    </div>}
  </section>;
}
