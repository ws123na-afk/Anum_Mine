/** The PA daily briefing (docs/ANUM_MINE_EXECUTIVE_PA.md#daily-briefing). It only shows what the
 * server built from real records: each section is a fixed sentence plus up to five records, and each
 * record opens its source. A section with no records says "Nothing to report" and one that could not
 * be read says "Not connected" (both sentences come from the server). Nothing here can decide an approval. */
import { RefreshCw } from 'lucide-react';
import { formatDateTime, t } from './lib/i18n';
import type { Briefing, BriefingItem, BriefingSection } from './lib/executivePa';

export interface BriefingOpen { view: string; id: string; kind: string }

const stamp = (value: string | null): string => (value ? formatDateTime(value, { timeStyle: 'short', dateStyle: 'medium' }) : '');

function itemMeta(section: BriefingSection, item: BriefingItem): string {
  const parts: string[] = [];
  if (item.detail_text) parts.push(item.detail_text);
  if (item.at) parts.push(t(section.key === 'due' ? 'pa.briefing.itemDue' : 'pa.briefing.itemAt', { time: stamp(item.at) }));
  return parts.join(' | ');
}

export function PaBriefingCard({ briefing, busy, onOpen, onRefresh }: {
  briefing: Briefing; busy: boolean; onOpen(target: BriefingOpen): void; onRefresh(): void;
}) {
  return <section className="paBrief" aria-labelledby="pa-brief-title">
    <header className="paBriefHead">
      <div>
        <h3 id="pa-brief-title">{t('pa.briefing.title')}</h3>
        <p className="muted">{t('pa.briefing.asOf', { time: stamp(briefing.generated_at) })}</p>
      </div>
      <button type="button" className="secondary" disabled={busy} onClick={onRefresh} aria-label={t('pa.briefing.refresh')}><RefreshCw aria-hidden="true" /><span>{t('pa.briefing.refresh')}</span></button>
    </header>
    {briefing.reportable === 0 && <p className="paBriefClear" role="status">{t('pa.briefing.allClear')}</p>}
    <ol className="paBriefList">
      {briefing.sections.map((section) => <li key={section.key} className="paBriefSection" data-state={section.state} data-section={section.key}>
        <h4>{section.title}</h4>
        <p className="paBriefHeadline">{section.headline}</p>
        {section.note_texts.map((note) => <p key={note} className="paBriefNote muted">{note}</p>)}
        {section.key !== 'cost' && section.items.length > 0 && <ul className="paBriefItems">
          {section.items.map((item) => <li key={`${item.kind}:${item.id}`}>
            <button type="button" className="linkButton" onClick={() => onOpen({ view: item.link.view, id: item.link.id, kind: item.kind })}
              aria-label={t('pa.briefing.open', { label: item.label })}>
              <span className="paBriefLabel"><bdi>{item.label}</bdi></span>
              {itemMeta(section, item) && <small><bdi>{itemMeta(section, item)}</bdi></small>}
            </button>
          </li>)}
        </ul>}
        {(section.state === 'ok' && section.key === 'cost') && <button type="button" className="linkButton" onClick={() => onOpen({ view: 'budgets', id: 'workspace', kind: 'budget' })}>{t('pa.briefing.openBudgets')}</button>}
      </li>)}
    </ol>
    <p className="muted paBriefFoot">{t('pa.briefing.noDecision')}</p>
  </section>;
}
