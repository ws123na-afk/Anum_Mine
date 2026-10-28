/**
 * The Executive assistant dock with the same header as every other panel beside the scene: its title, one line on what it is for,
 * its live status and a fold button. The status comes from the assistant's own tools read and the workspace event link the shell
 * already keeps open (lib/hqPanelStatus.ts `assistantLive`); nothing here opens a connection.
 */
import type { ReactNode } from 'react';
import type { PanelLive } from '../lib/hqPanelStatus.ts';
import { t } from '../lib/i18n.ts';
import { LiveChip } from './LiveChip.tsx';
import { PanelSection } from './PanelSection.tsx';

export function AssistantPanel(props: Readonly<{ state: PanelLive; toolsFailed: boolean; nowMs: number; children: ReactNode }>) {
  const hint = props.toolsFailed ? t('hqPanels.assistant.toolsHint') : props.state === 'live' ? t('hqPanels.assistant.liveHint') : props.state === 'offline' ? t('hqPanels.assistant.offlineHint') : undefined;
  return (
    <PanelSection id="assistant" title={t('hq.paDock')} purpose={t('hqPanels.assistant.purpose')} className="hqAssistantPanel"
      live={<LiveChip state={props.state} at={null} staleAfterMs={Number.POSITIVE_INFINITY} nowMs={props.nowMs} hint={hint} testId="hq-assistant-live" />}>
      <div className="hqPaDock">{props.children}</div>
    </PanelSection>
  );
}
