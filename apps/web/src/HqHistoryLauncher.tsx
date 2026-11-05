import { History } from 'lucide-react';
import { showRecordHistory, type HistoryRecordRef } from './lib/hqHistoryLink';
import { t } from './lib/i18n';

/** The button that opens the office history for a room or an agent. Kept apart so the panel itself stays a lazy chunk. */
export function HistoryLauncher({ label, onClick }: { label: string; onClick: () => void }) {
  return <button type="button" className="hqGhost hqHistLaunch" onClick={onClick}>{label}</button>;
}

/**
 * "Show history" on a task, meeting or approval: opens the History panel (loaded on this click) at the item's request view.
 * `onOpen` lets a screen that would sit on top of the panel (the task side panel) close itself.
 */
export function ShowHistoryLink({ record, className = '', onOpen }: { record: HistoryRecordRef; className?: string; onOpen?: () => void }) {
  const kind = t(`hqHist.source.${record.recordKind}` as 'hqHist.source.task');
  return (
    <button type="button" className={`hqHistLink ${className}`.trim()} data-testid="hq-show-history"
      aria-label={t('hqHist.link.label', { kind, name: record.label ?? record.id })} onClick={() => { if (showRecordHistory(record)) onOpen?.(); }}>
      <History aria-hidden="true" />{t('hqHist.link.show')}
    </button>
  );
}
