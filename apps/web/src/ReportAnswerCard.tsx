// The card under a structured Assistant answer (docs/reports.md#chat-answer-cards): the key
// metrics with their change, the top highlights, and "View Full Report" for the stored report.
import { ArrowUpRight, FileBarChart } from 'lucide-react';
import type { ReportCard } from '@anum/contracts';
import { t } from './lib/i18n';
import { changeLabel, changeTone, metricValue, periodLabel, reportTypeLabel } from './lib/library';
import './library.css';

export function ReportAnswerCard({ card, onOpen }: { card: ReportCard; onOpen?: (reportId: string) => void }) {
  const period = periodLabel(card.period_start, card.period_end);
  return <section className="reportCard" aria-label={t('reports.card.label', { title: card.title })}>
    <header>
      <FileBarChart aria-hidden="true" />
      <div><h3 dir="auto">{card.title}</h3><small>{reportTypeLabel(card.type)}{period ? ` · ${period}` : ''}</small></div>
    </header>
    {card.metrics.length > 0 && <dl className="reportMetrics">{card.metrics.map((metric, index) => {
      const change = changeLabel(metric.change_percent);
      return <div key={`${metric.label}-${index}`}><dt dir="auto">{metric.label}</dt><dd><strong dir="auto">{metricValue(metric)}</strong>{change && <span className={`reportChange ${changeTone(metric.change_percent)}`} dir="ltr">{change}</span>}</dd></div>;
    })}</dl>}
    {card.highlights.length > 0 && <ul className="reportHighlights">{card.highlights.map((h, i) => <li key={i} dir="auto">{h}</li>)}</ul>}
    {onOpen && <div className="actions"><button type="button" onClick={() => onOpen(card.report_id)}>{t('reports.card.view')}<ArrowUpRight aria-hidden="true" /></button></div>}
  </section>;
}
