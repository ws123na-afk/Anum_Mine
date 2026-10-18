// The post-close daily trading reports (docs/finance-trading-intel.md#daily-report): a paged list, newest
// first, and the selected report. Read only and SIMULATED. Opened from the report notice in the bell
// (`#finance-intel-reports`, newest selected) or by link (`#finance-intel-report=<id>`).
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { ChevronLeft, ChevronRight, Download, FileText, X } from 'lucide-react';
import type { FinanceDailyReport, FinanceDailyReportItem, FinancePage } from '@anum/contracts';
import * as api from './lib/financeApi';
import { formatAmounts, isolate, rangeLabel } from './lib/finance';
import { agentLabel, outcomeLabel, outcomeTone } from './lib/financeIntel';
import { REPORT_PAGE, markdownName, needsAction, pct, reportFromHash, reportHash, sectionCounts, summaryLine, summaryRows } from './lib/financeReports';
import { formatDateTime, t } from './lib/i18n';
import { Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import './financeReports.css';

const currentHash = () => (typeof location === 'undefined' ? '' : location.hash);

export function DailyReports({ refresh }: { refresh: number }) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Load<FinancePage<FinanceDailyReportItem>>>({ kind: 'loading' });
  const [selected, setSelected] = useState<string | null>(() => reportFromHash(currentHash()));
  const section = useRef<HTMLElement>(null);
  const load = useCallback(async () => {
    setPage((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    try { setPage({ kind: 'ready', value: await api.listDailyReports({ limit: REPORT_PAGE, offset }) }); } catch (e) { setPage(failed(e)); }
  }, [offset, refresh]);
  useEffect(() => { void load(); }, [load]);
  // A deep link (the bell's notice) brings the reports into view.
  useEffect(() => {
    const sync = () => {
      const target = reportFromHash(currentHash());
      if (target === null) return;
      setSelected(target);
      section.current?.scrollIntoView({ block: 'start' });
    };
    sync();
    window.addEventListener('hashchange', sync);
    return () => window.removeEventListener('hashchange', sync);
  }, []);
  // `''` means "the newest": pick it once the list is in.
  const items = page.kind === 'ready' ? page.value.items : [];
  const openId = selected === '' ? items[0]?.id ?? null : selected;
  const pick = (id: string | null) => {
    setSelected(id);
    if (id) history.replaceState(null, '', reportHash(id));
    else if (reportFromHash(currentHash()) !== null) history.replaceState(null, '', '#finance-intel');
  };
  return <section ref={section} className="surface cmPanel finReports" aria-labelledby="fin-intel-reports">
    <div className="sectionHeader"><h2 id="fin-intel-reports"><FileText aria-hidden="true" className="cmInlineIcon" />{t('finance.reports.title')}</h2></div>
    <p className="muted finHelp">{t('finance.reports.help')}</p>
    {page.kind === 'loading' && <Notice busy>{t('finance.reports.loading')}</Notice>}
    {page.kind === 'denied' && <Notice error>{t('finance.denied.text')}</Notice>}
    {page.kind === 'error' && <LoadError message={t('finance.reports.error', { message: page.message })} retry={() => void load()} />}
    {page.kind === 'ready' && page.value.total === 0 && <Empty icon={<FileText aria-hidden="true" />} title={t('finance.reports.empty')} text={t('finance.reports.emptyText')} />}
    {page.kind === 'ready' && items.length > 0 && <div className="finReportsBody">
      <div className="finReportsList">
        <ol className="cmList" aria-label={t('finance.reports.list')}>{items.map((r) => <li key={r.id}>
          <button type="button" className={`finReportPick ${openId === r.id ? 'active' : ''}`} aria-pressed={openId === r.id}
            aria-label={t('finance.reports.open', { date: r.date })} onClick={() => pick(r.id)}>
            <strong dir="ltr">{r.date}</strong>
            <small className="muted">{summaryLine(r.summary)}</small>
            {needsAction(r.summary) && <span className="statusBadge warning">{t('finance.reports.needsAction')}</span>}
          </button>
        </li>)}</ol>
        {(page.value.total > REPORT_PAGE || offset > 0) && <nav className="cmPager" aria-label={t('finance.reports.pages')}>
          <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - REPORT_PAGE))}><ChevronLeft aria-hidden="true" className="cmFlip" />{t('finance.page.previous')}</button>
          <span className="muted" aria-live="polite">{rangeLabel(offset, items.length, page.value.total)}</span>
          <button type="button" className="secondary" disabled={offset + items.length >= page.value.total} onClick={() => setOffset(offset + REPORT_PAGE)}>{t('finance.page.next')}<ChevronRight aria-hidden="true" className="cmFlip" /></button>
        </nav>}
      </div>
      {openId && <ReportDetail key={openId} reportId={openId} onClose={() => pick(null)} />}
    </div>}
  </section>;
}

function ReportDetail({ reportId, onClose }: { reportId: string; onClose: () => void }) {
  const [state, setState] = useState<Load<FinanceDailyReport>>({ kind: 'loading' });
  const load = useCallback(async () => {
    setState({ kind: 'loading' });
    try { setState({ kind: 'ready', value: await api.getDailyReport(reportId) }); } catch (e) { setState(failed(e)); }
  }, [reportId]);
  useEffect(() => { void load(); }, [load]);
  const headingId = useId();
  return <article className="finReport" aria-labelledby={headingId}>
    {state.kind === 'loading' && <Notice busy>{t('finance.reports.loadingOne')}</Notice>}
    {(state.kind === 'error' || state.kind === 'denied') && <LoadError message={t('finance.reports.errorOne', { message: state.message })} retry={() => void load()} />}
    {state.kind === 'ready' && <ReportBody report={state.value} headingId={headingId} onClose={onClose} />}
  </article>;
}

function download(report: FinanceDailyReport) {
  const url = URL.createObjectURL(new Blob([report.markdown], { type: 'text/markdown;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url; link.download = markdownName(report.date);
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function ReportBody({ report: r, headingId, onClose }: { report: FinanceDailyReport; headingId: string; onClose: () => void }) {
  const body = r.report, counts = sectionCounts(body);
  const none = <p className="muted">{t('finance.reports.none')}</p>;
  return <>
    <div className="finReportHead">
      <h3 id={headingId}>{t('finance.reports.selected', { date: r.date })} <span className="statusBadge warning">SIMULATED</span></h3>
      <div className="actions">
        <button type="button" className="secondary" onClick={() => download(r)}><Download aria-hidden="true" />{t('finance.reports.download')}</button>
        <button type="button" className="secondary iconButton" aria-label={t('finance.reports.close')} onClick={onClose}><X aria-hidden="true" /></button>
      </div>
    </div>
    <p className="muted finHelp" dir="auto">{body.disclaimer} <small>{formatDateTime(r.created_at)}</small></p>
    <dl className="finFigures finReportFigures">{summaryRows(body.summary).map((row) => <div key={row.key}><dt>{row.label}</dt><dd dir="ltr">{row.value}</dd></div>)}</dl>
    <ReportSection title={t('finance.reports.next')}>
      <ul className="finIntelReasons">{body.next_steps.map((step, i) => <li key={i} dir="auto">{step}</li>)}</ul>
    </ReportSection>
    <ReportSection title={t('finance.reports.orders')} count={counts.orders}>
      {body.orders.length ? <ul className="cmList">{body.orders.map((o, i) => <li key={i}><div>
        <p><strong dir="auto">{o.strategy}</strong> <span dir="ltr">{o.side} {String(o.quantity ?? '')} {o.symbol}</span></p>
        <small className="muted">{t('finance.reports.orderLine', { target: pct(o.target_weight), fill: isolate(`${o.estimate?.fill_price ?? '-'} (${o.estimate?.price_source ?? '-'})`), fee: o.estimate?.fee ?? '-', guardian: o.guardian ?? '-' })}</small>
      </div></li>)}</ul> : none}
    </ReportSection>
    <ReportSection title={t('finance.reports.refused')} count={counts.refused}>
      {counts.refused ? <ul className="cmList">
        {body.refused.map((x, i) => <li key={`r${i}`}><div><p><strong dir="auto">{x.strategy}</strong> <small className="muted">{t('finance.reports.refusedBy', { agent: agentLabel(x.agent) })}</small></p>
          <ul className="finIntelReasons">{x.reasons.map((reason, j) => <li key={j} dir="auto">{reason}</li>)}</ul></div></li>)}
        {body.suspended.map((x, i) => <li key={`s${i}`}><div><p><strong dir="auto">{x.strategy}</strong> <small className="muted">{t('finance.reports.suspendedBy', { agent: agentLabel(x.agent) })}</small></p>
          <ul className="finIntelReasons">{[x.why, ...x.reasons].filter((v): v is string => Boolean(v)).map((reason, j) => <li key={j} dir="auto">{reason}</li>)}</ul></div></li>)}
      </ul> : none}
    </ReportSection>
    <ReportSection title={t('finance.reports.data')} count={counts.data}>
      {body.data_health.length ? <ul className="cmList">{body.data_health.map((d, i) => <li key={i}><div>
        <p><strong dir="ltr">{d.symbol ?? '-'}</strong> <small className="muted" dir="ltr">{d.venue ?? ''}</small> <span className={`statusBadge ${outcomeTone(d.status)}`}>{outcomeLabel(d.status)}</span></p>
        <small className="muted">{t('finance.reports.dataLine', { bar: isolate(d.last_bar ?? '-'), behind: d.sessions_behind ?? '-', source: isolate(d.source ?? '-') })}</small>
        {d.reasons.length > 0 && <ul className="finIntelReasons">{d.reasons.map((reason, j) => <li key={j} dir="auto">{reason}</li>)}</ul>}
      </div></li>)}</ul> : none}
    </ReportSection>
    <ReportSection title={t('finance.reports.portfolios')} count={counts.portfolios}>
      {body.portfolios.length ? <ul className="cmList">{body.portfolios.map((p, i) => <li key={i}><div>
        <p><strong dir="auto">{p.strategy}</strong></p>
        <small className="muted">{t('finance.reports.portfolioLine', { cash: isolate(formatAmounts(p.cash ?? [])), realised: isolate(formatAmounts(p.realised ?? [])), unrealised: isolate(formatAmounts(p.unrealised ?? [])), fees: isolate(formatAmounts(p.fees ?? [])) })}</small>
        {p.unpriced && p.unpriced.length > 0 && <p className="muted">{t('finance.reports.unpriced', { list: isolate(p.unpriced.join(', ')) })}</p>}
      </div></li>)}</ul> : none}
    </ReportSection>
    <ReportSection title={t('finance.reports.performance')} count={counts.performance}>
      {body.performance.length ? <ul className="cmList">{body.performance.map((p, i) => <li key={i}><div>
        <p><strong dir="auto">{p.strategy}</strong></p>
        <small className="muted">{t('finance.reports.performanceLine', { since: isolate(p.since ?? '-'), ret: isolate(pct(p.return)), bench: isolate(pct(p.benchmark_return)), drawdown: isolate(pct(p.max_drawdown)) })}</small>
        {p.flags.length > 0 && <ul className="finIntelReasons">{p.flags.map((f, j) => <li key={j} dir="auto">{f.replace(/_/g, ' ')}</li>)}</ul>}
      </div></li>)}</ul> : none}
    </ReportSection>
    <ReportSection title={t('finance.reports.problems')} count={counts.problems}>
      {body.problems.length ? <ul className="cmList">{body.problems.map((p, i) => <li key={i}><div>
        <p><strong>{agentLabel(p.agent)}</strong> <span dir="auto">{p.action.replace(/_/g, ' ')}</span> <span className={`statusBadge ${outcomeTone(p.outcome)}`}>{outcomeLabel(p.outcome)}</span></p>
        {p.reasons.length > 0 && <ul className="finIntelReasons">{p.reasons.map((reason, j) => <li key={j} dir="auto">{reason}</li>)}</ul>}
      </div></li>)}</ul> : none}
    </ReportSection>
    <p className="muted finHelp">{t('finance.reports.recorded')}</p>
  </>;
}

function ReportSection({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  const id = useId();
  return <section className="finReportSection" aria-labelledby={id}>
    <h4 id={id}>{title}{count !== undefined && <span className="muted"> ({count})</span>}</h4>
    {children}
  </section>;
}
