// The workspace audit log (docs/governance-and-scale.md, Audit log): filters, keyset paging,
// a detail drawer and CSV/JSON export of the filtered range. Owners and organization
// administrators may read it; everyone else gets the API's 403, explained in place.
import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { CircleDot, Download, Filter, Lock, RefreshCw, RotateCcw, ScrollText, Search } from 'lucide-react';
import type { AuditFilters, AuditRecord } from '@anum/contracts';
import * as admin from './lib/adminApi';
import {
  AUDIT_PAGE_SIZE,
  EMPTY_FILTER_DRAFT,
  actionLabel,
  actionOptions,
  auditFilters,
  hasFilters,
  metadataRows,
  outcomeTone,
  type AuditFilterDraft,
} from './lib/audit';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import { Drawer } from './Dialog';
import './admin.css';
import './audit.css';
import { HelpLink } from './HelpLink';

type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'forbidden'; detail: string } | { kind: 'error'; detail: string };

const when = (value: string) => formatDateTime(value, { dateStyle: 'medium', timeStyle: 'medium' });
const outcomeText = (outcome: string) => outcome === 'success' ? t('audit.outcome.success') : outcome === 'refused_too_large' ? t('audit.outcome.refused') : outcome.replaceAll('_', ' ');

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

export function AuditLogView({ onOpenMembers }: { onOpenMembers?: () => void }) {
  const [draft, setDraft] = useState<AuditFilterDraft>(EMPTY_FILTER_DRAFT);
  const [filters, setFilters] = useState<AuditFilters>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [records, setRecords] = useState<AuditRecord[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [retention, setRetention] = useState('');
  const [actions, setActions] = useState<string[]>([]);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [more, setMore] = useState<'idle' | 'loading' | { error: string }>('idle');
  const [selected, setSelected] = useState<AuditRecord | null>(null);
  const [exporting, setExporting] = useState<'csv' | 'json' | null>(null);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const request = useRef(0);

  const fetchFirst = useCallback(async (next: AuditFilters) => {
    const id = ++request.current;
    setLoad({ kind: 'loading' });
    setMore('idle');
    try {
      const page = await admin.getAuditPage(next, null, AUDIT_PAGE_SIZE);
      if (id !== request.current) return;
      setRecords(page.items);
      setCursor(page.nextCursor);
      setRetention(page.retention);
      setLoad({ kind: 'ready' });
    } catch (error) {
      if (id !== request.current) return;
      setLoad(isPermissionDenied(error) ? { kind: 'forbidden', detail: describeError(error) } : { kind: 'error', detail: describeError(error) });
    }
  }, []);

  useEffect(() => { void fetchFirst({}); }, [fetchFirst]);
  useEffect(() => { admin.getAuditActions().then(setActions).catch(() => setActions([])); }, []);

  const apply = (next: AuditFilters) => { setFilters(next); void fetchFirst(next); };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const result = auditFilters(draft);
    if (!result.ok) { setFormError(result.error); return; }
    setFormError(null);
    apply(result.value);
  };
  const reset = () => { setDraft(EMPTY_FILTER_DRAFT); setFormError(null); apply({}); };
  const narrow = (patch: Partial<AuditFilterDraft>) => {
    const nextDraft = { ...draft, ...patch };
    const result = auditFilters(nextDraft);
    if (!result.ok) return;
    setDraft(nextDraft);
    setSelected(null);
    apply(result.value);
  };

  async function loadMore() {
    if (!cursor) return;
    setMore('loading');
    try {
      const page = await admin.getAuditPage(filters, cursor, AUDIT_PAGE_SIZE);
      setRecords((current) => [...current, ...page.items]);
      setCursor(page.nextCursor);
      setMore('idle');
    } catch (error) {
      setMore({ error: describeError(error) });
    }
  }

  async function download(format: 'csv' | 'json') {
    setExporting(format);
    setFlash(null);
    try {
      const name = await admin.downloadAuditExport(filters, format);
      setFlash({ text: t(hasFilters(filters) ? 'audit.downloadedFiltered' : 'audit.downloaded', { name }) });
    } catch (error) {
      setFlash({ text: isPermissionDenied(error) ? t('audit.exportDenied', { message: describeError(error) }) : describeError(error), error: true });
    } finally {
      setExporting(null);
    }
  }

  const filtered = hasFilters(filters);
  const options = actionOptions(actions);
  const set = (key: keyof AuditFilterDraft) => (event: { target: { value: string } }) => setDraft({ ...draft, [key]: event.target.value });

  return <>
    <section className="moduleIntro">
      <div><p className="eyebrow">{t('audit.eyebrow')}</p><h2>{t('audit.title')}</h2><p>{t('audit.intro')}</p></div>
      <div className="actions">
        <button type="button" className="secondary" disabled={exporting !== null || load.kind !== 'ready'} onClick={() => void download('csv')}><Download aria-hidden="true" />{exporting === 'csv' ? t('audit.exporting') : t('audit.exportCsv')}</button>
        <button type="button" className="secondary" disabled={exporting !== null || load.kind !== 'ready'} onClick={() => void download('json')}><Download aria-hidden="true" />{exporting === 'json' ? t('audit.exporting') : t('audit.exportJson')}</button>
      </div>
    </section>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}

    <form className="surface auditFilters" aria-label={t('audit.filterForm')} noValidate onSubmit={submit}>
      <div className="auditFilterGrid">
        <label className="field auditSearch"><span>{t('common.search')}</span><input type="search" value={draft.q} onChange={set('q')} placeholder={t('audit.searchPlaceholder')} autoComplete="off" /></label>
        <label className="field"><span>{t('audit.actor')}</span><input value={draft.actor} onChange={set('actor')} placeholder={t('audit.actorPlaceholder')} autoComplete="off" spellCheck={false} dir="ltr" /></label>
        <label className="field"><span>{t('audit.action')}</span>
          <select value={draft.action} onChange={set('action')}>
            <option value="">{t('audit.anyAction')}</option>
            {draft.action && !options.some((option) => option.value === draft.action) && <option value={draft.action}>{draft.action}</option>}
            {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <label className="field"><span>{t('audit.target')}</span><input value={draft.target} onChange={set('target')} placeholder={t('audit.targetPlaceholder')} autoComplete="off" spellCheck={false} dir="ltr" /></label>
        <label className="field"><span>{t('audit.outcome')}</span>
          <select value={draft.outcome} onChange={set('outcome')}>
            <option value="">{t('audit.anyOutcome')}</option><option value="success">{t('audit.outcome.success')}</option><option value="refused_too_large">{t('audit.outcome.refused')}</option>
          </select>
        </label>
        <label className="field"><span>{t('audit.from')}</span><input type="date" value={draft.from} onChange={set('from')} /></label>
        <label className="field"><span>{t('audit.to')}</span><input type="date" value={draft.to} onChange={set('to')} /></label>
      </div>
      {draft.correlationId && <p className="muted auditCorrelation">{t('audit.correlationOnly', { id: draft.correlationId })} <button type="button" className="linkButton" onClick={() => narrow({ correlationId: '' })}>{t('common.clear')}</button></p>}
      {formError && <Notice error>{formError}</Notice>}
      <div className="actions">
        <button type="submit" disabled={load.kind === 'loading'}><Filter aria-hidden="true" />{t('audit.apply')}</button>
        <button type="button" className="secondary" onClick={reset}><RotateCcw aria-hidden="true" />{t('audit.reset')}</button>
      </div>
    </form>

    {load.kind === 'loading' && <Notice>{t('audit.loading')}</Notice>}
    {load.kind === 'error' && <section className="surface adminForbidden" aria-label={t('audit.unavailable')}>
      <span className="adminIcon"><ScrollText aria-hidden="true" /></span>
      <div><h3>{t('audit.errorTitle')}</h3><p>{load.detail}</p><p className="muted">{t('audit.errorHelp')}</p>
        <div className="actions"><button type="button" onClick={() => void fetchFirst(filters)}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>
    </section>}
    {load.kind === 'forbidden' && <section className="surface adminForbidden" aria-label={t('audit.accessRegion')}>
      <span className="adminIcon"><Lock aria-hidden="true" /></span>
      <div><h3>{t('audit.deniedTitle')}</h3>
        <p>{t('audit.deniedText')}</p>
        <p className="muted">{t('audit.apiAnswered', { message: load.detail })}</p></div>
    </section>}

    {load.kind === 'ready' && <section className="surface auditPanel" aria-label={t('audit.table')}>
      <div className="sectionHeader">
        <div><h2>{t(cursor ? 'audit.recordsLoaded' : 'audit.records', { count: records.length })}</h2><p className="muted">{filtered ? t('audit.newestFirstFiltered') : t('audit.newestFirst')}</p></div>
      </div>
      {!records.length && (filtered
        ? <div className="emptyState"><Search aria-hidden="true" /><h3>{t('audit.noMatchTitle')}</h3><p>{t('audit.noMatchText')}</p><button type="button" className="secondary" onClick={reset}>{t('audit.resetFilters')}</button></div>
        : <div className="emptyState"><ScrollText aria-hidden="true" /><h3>{t('audit.emptyTitle')}</h3><p>{t('audit.emptyText')}</p><HelpLink />{onOpenMembers && <button type="button" className="secondary" onClick={onOpenMembers}>{t('audit.manageMembers')}</button>}</div>)}
      {records.length > 0 && <div className="auditTable" role="table" aria-label={t('audit.table')} aria-rowcount={records.length + 1}>
        <div className="auditRow auditHead" role="row">
          <span role="columnheader">{t('audit.when')}</span><span role="columnheader">{t('audit.actor')}</span><span role="columnheader">{t('audit.action')}</span><span role="columnheader">{t('audit.target')}</span><span role="columnheader">{t('audit.outcome')}</span>
        </div>
        {records.map((record) => <div className="auditRow" role="row" key={record.id}>
          <span role="cell" className="auditWhen"><button type="button" className="linkButton" aria-label={t('audit.detailsOf', { action: record.action, actor: record.actor, when: when(record.createdAt) }, { isolate: false })} onClick={() => setSelected(record)}>{when(record.createdAt)}</button></span>
          <span role="cell" className="auditActor" data-label={t('audit.actor')} dir="ltr">{record.actor}</span>
          <span role="cell" data-label={t('audit.action')}><strong>{actionLabel(record.action)}</strong><small dir="ltr">{record.action}</small></span>
          <span role="cell" className="auditTarget" data-label={t('audit.target')} dir="ltr">{record.target}</span>
          <span role="cell" data-label={t('audit.outcome')}><span className={`statusBadge ${outcomeTone(record.outcome)}`}>{outcomeText(record.outcome)}</span></span>
        </div>)}
      </div>}
      {cursor && <div className="actions auditMore">
        <button type="button" className="secondary" disabled={more === 'loading'} onClick={() => void loadMore()}>{more === 'loading' ? t('common.loading') : t('audit.loadOlder')}</button>
      </div>}
      {typeof more === 'object' && <Notice error>{t('audit.moreError', { message: more.error })}</Notice>}
      <p className="muted auditRetention"><strong>{t('audit.retentionLabel')}</strong> {retention || t('audit.defaultRetention')}</p>
    </section>}

    {selected && <Drawer title={t('audit.recordTitle')} onClose={() => setSelected(null)}>
      <dl className="auditDetail">
        <div><dt>{t('audit.when')}</dt><dd>{when(selected.createdAt)}<small dir="ltr">{selected.createdAt}</small></dd></div>
        <div><dt>{t('audit.actor')}</dt><dd><code>{selected.actor}</code></dd></div>
        <div><dt>{t('audit.action')}</dt><dd>{actionLabel(selected.action)}<small><code>{selected.action}</code></small></dd></div>
        <div><dt>{t('audit.target')}</dt><dd><code>{selected.target}</code></dd></div>
        <div><dt>{t('audit.outcome')}</dt><dd><span className={`statusBadge ${outcomeTone(selected.outcome)}`}>{outcomeText(selected.outcome)}</span></dd></div>
        <div><dt>{t('audit.correlationId')}</dt><dd><code>{selected.correlationId}</code></dd></div>
        <div><dt>{t('audit.recordId')}</dt><dd><code>{selected.id}</code></dd></div>
        <div><dt>{t('audit.workspace')}</dt><dd><code>{selected.workspaceId}</code></dd></div>
      </dl>
      <h3 className="auditMetaTitle">{t('audit.detailsHeading')}</h3>
      {metadataRows(selected.metadata).length
        ? <dl className="auditMeta">{metadataRows(selected.metadata).map((row) => <div key={row.key}><dt dir="ltr">{row.key}</dt><dd><code>{row.value}</code></dd></div>)}</dl>
        : <p className="muted">{t('audit.noDetails')}</p>}
      <div className="actions">
        <button type="button" className="secondary" onClick={() => narrow({ actor: selected.actor })}>{t('audit.onlyActor')}</button>
        <button type="button" className="secondary" onClick={() => narrow({ target: selected.target })}>{t('audit.onlyTarget')}</button>
        <button type="button" className="secondary" onClick={() => narrow({ correlationId: selected.correlationId })}>{t('audit.sameCorrelation')}</button>
      </div>
    </Drawer>}
  </>;
}
