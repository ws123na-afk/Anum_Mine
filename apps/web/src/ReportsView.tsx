// Report centre (docs/reports.md): every generated report with filters, search and paging; a
// detail drawer with key metrics, highlights and the data snapshot; CSV and Markdown downloads,
// print or save as PDF, rename, share with the workspace or keep private, delete; generate a
// report now or on a schedule (an automation with a `report.generate` step).
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { CalendarClock, CircleDot, Download, FileBarChart, Link2, Lock, MessageSquare, Printer, RefreshCw, Search, Trash2, Users } from 'lucide-react';
import type { Report, ReportCreatedByKind, ReportGenerator, ReportPage, ReportSummary, Visibility } from '@anum/contracts';
import { ConfirmDialog, Drawer } from './Dialog';
import { StarButton, useStars } from './Favorites';
import { ReportShare } from './ReportShare';
import { ApiError, describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import { REPORTS_PAGE, changeLabel, changeTone, createdByLabel, metricValue, periodLabel, reportTypeLabel } from './lib/library';
import * as lib from './lib/libraryApi';
import './library.css';
import { HelpLink } from './HelpLink';

type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'forbidden'; detail: string } | { kind: 'error'; detail: string };
type Flash = { text: string; error?: boolean } | null;

const SCHEDULES = [
  { cron: '0 9 * * *', key: 'reports.schedule.daily' },
  { cron: '0 9 * * 1', key: 'reports.schedule.weekly' },
  { cron: '0 9 1 * *', key: 'reports.schedule.monthly' },
] as const;
const PERIODS = [1, 7, 30, 90] as const;

function Notice({ flash }: { flash: Flash }) {
  if (!flash) return null;
  return <p className={`notice ${flash.error ? 'errorNotice' : ''}`} role={flash.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{flash.text}</p>;
}

export function Metrics({ metrics }: { metrics: ReportSummary['metrics'] }) {
  return <dl className="reportMetrics">{metrics.map((metric, index) => {
    const change = changeLabel(metric.change_percent);
    return <div key={`${metric.label}-${index}`}>
      <dt dir="auto">{metric.label}</dt>
      <dd><strong dir="auto">{metricValue(metric)}</strong>{change && <span className={`reportChange ${changeTone(metric.change_percent)}`} dir="ltr">{change}</span>}</dd>
    </div>;
  })}</dl>;
}

export function ReportsView({ initialReportId, onOpenConversation, onOpenAutomation, onOpenApprovals, onOpenInbox }: {
  initialReportId?: string | null; onOpenConversation?: (id: string) => void; onOpenAutomation?: () => void;
  onOpenApprovals?: () => void; onOpenInbox?: () => void;
}) {
  const [filters, setFilters] = useState<lib.ReportQuery>({});
  const [draft, setDraft] = useState('');
  const [page, setPage] = useState<ReportPage | null>(null);
  const [items, setItems] = useState<ReportSummary[]>([]);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [more, setMore] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  const [openId, setOpenId] = useState<string | null>(initialReportId ?? null);
  const [generators, setGenerators] = useState<ReportGenerator[]>([]);
  const stars = useStars('report');
  const request = useRef(0);

  const fetchFirst = useCallback(async (next: lib.ReportQuery) => {
    const id = ++request.current;
    setLoad({ kind: 'loading' });
    try {
      const result = await lib.listReports(next, 0, REPORTS_PAGE);
      if (id !== request.current) return;
      setPage(result); setItems(result.items); setLoad({ kind: 'ready' });
    } catch (e) {
      if (id !== request.current) return;
      setLoad(isPermissionDenied(e) ? { kind: 'forbidden', detail: describeError(e) } : { kind: 'error', detail: describeError(e) });
    }
  }, []);
  useEffect(() => { void fetchFirst(filters); }, [fetchFirst, filters]);
  useEffect(() => { lib.listGenerators().then(setGenerators).catch(() => setGenerators([])); }, []);

  async function loadMore() {
    setMore(true);
    try {
      const result = await lib.listReports(filters, items.length, REPORTS_PAGE);
      setPage(result); setItems((current) => [...current, ...result.items]);
    } catch (e) { setFlash({ text: describeError(e), error: true }); }
    finally { setMore(false); }
  }

  const open = (id: string | null) => {
    setOpenId(id);
    history.replaceState(null, '', id ? `#reports/${encodeURIComponent(id)}` : '#reports');
  };
  const search = (event: FormEvent) => { event.preventDefault(); setFilters({ ...filters, q: draft.trim() || undefined }); };
  const filtered = Boolean(filters.q || filters.type || filters.created_by_kind);

  return <>
    <section className="moduleIntro">
      <div><p className="eyebrow">{t('reports.eyebrow')}</p><h2>{t('reports.title')}</h2><p>{t('reports.intro')}</p></div>
    </section>
    <Notice flash={flash} />
    {generators.length > 0 && <GeneratePanel generators={generators} onGenerated={(report) => { setFlash({ text: t('reports.generated', { title: report.title }) }); void fetchFirst(filters); open(report.id); }} onScheduled={(name) => setFlash({ text: t('reports.schedule.done', { name }) })} onOpenAutomation={onOpenAutomation} />}

    <section className="surface libraryPanel" aria-labelledby="reports-list-title" aria-busy={load.kind === 'loading'}>
      <div className="libraryPanelHead">
        <h3 id="reports-list-title">{t('reports.listTitle')}{page && load.kind === 'ready' ? <span className="muted"> · {t('reports.count', { n: page.total })}</span> : null}</h3>
        <button type="button" className="iconButton" aria-label={t('reports.refresh')} title={t('reports.refresh')} onClick={() => void fetchFirst(filters)}><RefreshCw aria-hidden="true" /></button>
      </div>
      <form className="libraryFilters" role="search" onSubmit={search}>
        <label className="field librarySearch"><span>{t('common.search')}</span><input type="search" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={t('reports.searchPlaceholder')} maxLength={200} /></label>
        <label className="field"><span>{t('reports.filterType')}</span>
          <select value={filters.type ?? ''} onChange={(e) => setFilters({ ...filters, type: e.target.value || undefined })}>
            <option value="">{t('reports.anyType')}</option>
            {(page?.types ?? []).map((entry) => <option key={entry.type} value={entry.type}>{reportTypeLabel(entry.type)} ({entry.count})</option>)}
          </select>
        </label>
        <label className="field"><span>{t('reports.filterBy')}</span>
          <select value={filters.created_by_kind ?? ''} onChange={(e) => setFilters({ ...filters, created_by_kind: (e.target.value || undefined) as ReportCreatedByKind | undefined })}>
            <option value="">{t('reports.anyone')}</option>
            {(['user', 'agent', 'automation'] as const).map((kind) => <option key={kind} value={kind}>{createdByLabel(kind)}</option>)}
          </select>
        </label>
        <div className="actions libraryFilterActions"><button type="submit"><Search aria-hidden="true" />{t('common.search')}</button>{filtered && <button type="button" className="secondary" onClick={() => { setDraft(''); setFilters({}); }}>{t('common.clear')}</button>}</div>
      </form>
      {stars.error && <Notice flash={{ text: stars.error, error: true }} />}
      {load.kind === 'loading' && <p className="muted libraryState" role="status">{t('reports.loading')}</p>}
      {load.kind === 'forbidden' && <div className="libraryState" role="alert"><Lock aria-hidden="true" /><p>{t('reports.forbidden', { message: load.detail })}</p></div>}
      {load.kind === 'error' && <div className="libraryState" role="alert"><p>{t('reports.loadFailed', { message: load.detail })}</p><button type="button" className="secondary" onClick={() => void fetchFirst(filters)}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
      {load.kind === 'ready' && items.length === 0 && <div className="emptyState"><FileBarChart aria-hidden="true" />
        <h3>{filtered ? t('reports.noMatch') : t('reports.empty')}</h3><p>{filtered ? t('reports.noMatchText') : t('reports.emptyText')}</p><HelpLink />
      </div>}
      {load.kind === 'ready' && items.length > 0 && <ul className="libraryList">{items.map((report) => <li key={report.id} className="libraryRow reportRow">
        <span className="libraryMain">
          <button type="button" className="linkButton libraryTitle" dir="auto" onClick={() => open(report.id)}>{report.title}</button>
          <small>
            <span className="statusBadge neutral">{reportTypeLabel(report.type)}</span>
            {periodLabel(report.period_start, report.period_end) && <span>{periodLabel(report.period_start, report.period_end)}</span>}
            <span>{createdByLabel(report.created_by_kind)} · {formatDateTime(report.created_at, { dateStyle: 'medium', timeStyle: 'short' })}</span>
            {report.visibility === 'private' && <span className="libraryPrivate"><Lock aria-hidden="true" />{t('library.private')}</span>}
          </small>
          {report.metrics.length > 0 && <Metrics metrics={report.metrics.slice(0, 3)} />}
        </span>
        <StarButton starred={stars.ids.has(report.id)} label={report.title} onToggle={() => void stars.toggle(report.id)} />
      </li>)}</ul>}
      {load.kind === 'ready' && page && items.length < page.total && <div className="actions libraryMore">
        <button type="button" className="secondary" disabled={more} onClick={() => void loadMore()}>{more ? t('common.loading') : t('reports.more', { shown: items.length, total: page.total })}</button>
      </div>}
    </section>
    {openId && <ReportDrawer id={openId} onClose={() => open(null)} onChanged={() => void fetchFirst(filters)} onDeleted={(title) => { open(null); setFlash({ text: t('reports.deleted', { title }) }); void fetchFirst(filters); }} onOpenConversation={onOpenConversation} onOpenApprovals={onOpenApprovals} onOpenInbox={onOpenInbox} />}
  </>;
}

function GeneratePanel({ generators, onGenerated, onScheduled, onOpenAutomation }: {
  generators: ReportGenerator[]; onGenerated: (report: Report) => void; onScheduled: (name: string) => void; onOpenAutomation?: () => void;
}) {
  const [type, setType] = useState<ReportGenerator['type']>(generators[0].type);
  const [days, setDays] = useState<number>(7);
  const [visibility, setVisibility] = useState<Visibility>('workspace');
  const [cron, setCron] = useState<string>(SCHEDULES[1].cron);
  const [busy, setBusy] = useState<'generate' | 'schedule' | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const chosen = generators.find((g) => g.type === type) ?? generators[0];

  async function run(kind: 'generate' | 'schedule') {
    setBusy(kind); setProblem(null);
    try {
      if (kind === 'generate') onGenerated(await lib.generateReport({ type, period_days: days, visibility }));
      else onScheduled(await lib.scheduleReport(type, days, cron, timezone));
    } catch (e) {
      setProblem(isPermissionDenied(e) ? t(kind === 'generate' ? 'reports.generateDenied' : 'reports.schedule.denied') : t('reports.generateFailed', { message: describeError(e) }));
    } finally { setBusy(null); }
  }

  return <section className="surface libraryPanel reportGenerate" aria-labelledby="report-generate-title">
    <h3 id="report-generate-title">{t('reports.generateTitle')}</h3>
    <p className="muted">{t(`reports.generator.${chosen.type}`)}</p>
    <div className="libraryFilters">
      <label className="field"><span>{t('reports.generator')}</span>
        <select value={type} onChange={(e) => setType(e.target.value as ReportGenerator['type'])}>{generators.map((g) => <option key={g.type} value={g.type}>{reportTypeLabel(g.type)}</option>)}</select>
      </label>
      <label className="field"><span>{t('reports.periodDays')}</span>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>{PERIODS.map((n) => <option key={n} value={n}>{t('reports.lastDays', { n })}</option>)}</select>
      </label>
      <label className="field"><span>{t('reports.visibility')}</span>
        <select value={visibility} onChange={(e) => setVisibility(e.target.value as Visibility)}><option value="workspace">{t('library.workspace')}</option><option value="private">{t('library.privateToMe')}</option></select>
      </label>
      <label className="field"><span>{t('reports.schedule.when')}</span>
        <select value={cron} onChange={(e) => setCron(e.target.value)}>{SCHEDULES.map((s) => <option key={s.cron} value={s.cron}>{t(s.key)}</option>)}</select>
      </label>
    </div>
    {problem && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{problem}</p>}
    <div className="actions">
      <button type="button" disabled={busy !== null} onClick={() => void run('generate')}><FileBarChart aria-hidden="true" />{busy === 'generate' ? t('reports.generating') : t('reports.generateNow')}</button>
      <button type="button" className="secondary" disabled={busy !== null} onClick={() => void run('schedule')}><CalendarClock aria-hidden="true" />{busy === 'schedule' ? t('reports.schedule.saving') : t('reports.schedule.save')}</button>
      {onOpenAutomation && <button type="button" className="secondary" onClick={onOpenAutomation}>{t('reports.schedule.manage')}</button>}
    </div>
    <p className="muted reportScheduleNote">{t('reports.schedule.note', { timezone })}</p>
  </section>;
}

function ReportDrawer({ id, onClose, onChanged, onDeleted, onOpenConversation, onOpenApprovals, onOpenInbox }: {
  id: string; onClose: () => void; onChanged: () => void; onDeleted: (title: string) => void; onOpenConversation?: (id: string) => void;
  onOpenApprovals?: () => void; onOpenInbox?: () => void;
}) {
  const [report, setReport] = useState<Report | null>(null);
  const [state, setState] = useState<{ kind: 'loading' } | { kind: 'ready' } | { kind: 'missing' } | { kind: 'error'; detail: string }>({ kind: 'loading' });
  const [flash, setFlash] = useState<Flash>(null);
  const [title, setTitle] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const fetchReport = useCallback(async () => {
    setState({ kind: 'loading' });
    try { const value = await lib.getReport(id); setReport(value); setTitle(value.title); setState({ kind: 'ready' }); }
    catch (e) { setState(e instanceof ApiError && e.status === 404 ? { kind: 'missing' } : { kind: 'error', detail: describeError(e) }); }
  }, [id]);
  useEffect(() => { void fetchReport(); }, [fetchReport]);

  async function act(fn: () => Promise<void>) {
    setBusy(true); setFlash(null);
    try { await fn(); } catch (e) { setFlash({ text: isPermissionDenied(e) ? t('reports.manageDenied') : describeError(e), error: true }); }
    finally { setBusy(false); }
  }
  const rename = (event: FormEvent) => { event.preventDefault(); if (!report || !title.trim() || title.trim() === report.title) return; void act(async () => { setReport(await lib.updateReport(report.id, { title: title.trim() })); setFlash({ text: t('reports.renamed') }); onChanged(); }); };
  const share = (visibility: Visibility) => report && void act(async () => { setReport(await lib.updateReport(report.id, { visibility })); setFlash({ text: visibility === 'workspace' ? t('reports.shared') : t('reports.madePrivate') }); onChanged(); });
  const download = (format: 'csv' | 'md') => report && void act(async () => { setFlash({ text: t('reports.downloaded', { name: await lib.downloadReport(report, format) }) }); });
  const copyLink = () => report && void act(async () => {
    const url = `${location.origin}${location.pathname}#reports/${encodeURIComponent(report.id)}`;
    await navigator.clipboard.writeText(url);
    setFlash({ text: t('reports.linkCopied') });
  });

  const heading = report?.title ?? t('reports.detail');
  return <Drawer title={heading} onClose={onClose}>
    {state.kind === 'loading' && <p className="muted" role="status">{t('reports.loadingOne')}</p>}
    {state.kind === 'missing' && <div className="libraryState" role="alert"><p>{t('reports.missing')}</p></div>}
    {state.kind === 'error' && <div className="libraryState" role="alert"><p>{t('reports.loadFailed', { message: state.detail })}</p><button type="button" className="secondary" onClick={() => void fetchReport()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
    {state.kind === 'ready' && report && <div className="reportDetail" id="report-print">
      <p className="reportMeta">
        <span className="statusBadge neutral">{reportTypeLabel(report.type)}</span>
        {periodLabel(report.period_start, report.period_end) && <span>{periodLabel(report.period_start, report.period_end)}</span>}
        <span>{createdByLabel(report.created_by_kind)} · {formatDateTime(report.created_at, { dateStyle: 'medium', timeStyle: 'short' })}</span>
        <span>{report.visibility === 'private' ? <><Lock aria-hidden="true" />{t('library.private')}</> : <><Users aria-hidden="true" />{t('library.workspace')}</>}</span>
      </p>
      {report.summary && <p dir="auto">{report.summary}</p>}
      {report.metrics.length > 0 && <section aria-labelledby="report-metrics"><h3 id="report-metrics">{t('reports.keyMetrics')}</h3><Metrics metrics={report.metrics} /></section>}
      {report.highlights.length > 0 && <section aria-labelledby="report-highlights"><h3 id="report-highlights">{t('reports.highlights')}</h3><ul className="reportHighlights">{report.highlights.map((h, i) => <li key={i} dir="auto">{h}</li>)}</ul></section>}
      {report.data.columns.length > 0 && <section aria-labelledby="report-data"><h3 id="report-data">{t('reports.data')}</h3>
        <div className="reportTableWrap" tabIndex={0} role="region" aria-labelledby="report-data">
          <table className="reportTable"><thead><tr>{report.data.columns.map((c) => <th key={c} scope="col" dir="auto">{c}</th>)}</tr></thead>
            <tbody>{report.data.rows.slice(0, 100).map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j} dir="auto">{cell === null ? '' : String(cell)}</td>)}</tr>)}</tbody></table>
        </div>
        {report.data.rows.length > 100 && <p className="muted">{t('reports.rowsShown', { shown: 100, total: report.data.rows.length })}</p>}
      </section>}
      {report.source.conversation_id && onOpenConversation && <button type="button" className="linkButton reportSource" onClick={() => onOpenConversation(report.source.conversation_id!)}><MessageSquare aria-hidden="true" />{t('reports.openConversation')}</button>}
    </div>}
    {state.kind === 'ready' && report && <>
      <Notice flash={flash} />
      <div className="actions reportActions">
        <button type="button" className="secondary" disabled={busy} onClick={() => download('csv')}><Download aria-hidden="true" />{t('reports.downloadCsv')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => download('md')}><Download aria-hidden="true" />{t('reports.downloadMd')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => window.print()}><Printer aria-hidden="true" />{t('reports.print')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={copyLink}><Link2 aria-hidden="true" />{t('reports.copyLink')}</button>
      </div>
      {report.can_manage ? <section className="reportManage" aria-labelledby="report-manage">
        <h3 id="report-manage">{t('reports.manage')}</h3>
        <form className="reportRename" onSubmit={rename}>
          <label className="field"><span>{t('reports.rename')}</span><input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} required dir="auto" /></label>
          <div className="actions"><button type="submit" disabled={busy || !title.trim() || title.trim() === report.title}>{t('reports.saveName')}</button></div>
        </form>
        <div className="actions">
          {report.visibility === 'private'
            ? <button type="button" className="secondary" disabled={busy} onClick={() => share('workspace')}><Users aria-hidden="true" />{t('reports.shareWorkspace')}</button>
            : <button type="button" className="secondary" disabled={busy} onClick={() => share('private')}><Lock aria-hidden="true" />{t('reports.makePrivate')}</button>}
          <button type="button" className="danger" disabled={busy} onClick={() => setConfirming(true)}><Trash2 aria-hidden="true" />{t('common.delete')}</button>
        </div>
      </section> : <p className="muted">{t('reports.readOnly')}</p>}
      <ReportShare report={report} onOpenApprovals={onOpenApprovals} onOpenInbox={onOpenInbox} />
    </>}
    {confirming && report && <ConfirmDialog title={t('reports.deleteTitle')} confirmLabel={t('common.delete')} danger busy={busy}
      onCancel={() => setConfirming(false)}
      onConfirm={() => void act(async () => { await lib.deleteReport(report.id); setConfirming(false); onDeleted(report.title); })}>
      <p>{t('reports.deleteText', { title: report.title })}</p>
    </ConfirmDialog>}
  </Drawer>;
}
