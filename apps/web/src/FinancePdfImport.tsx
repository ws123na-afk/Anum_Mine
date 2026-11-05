// PDF statement review (docs/finance.md#pdf-statements). The API reads the PDF in memory and answers the rows,
// the period, the account's last four digits and the balance check; the person fixes the column mapping, edits or
// drops rows and imports them through the same path as a CSV (dedupe, SMS reconciliation, undo). The password,
// when the bank set one, lives in this component's state only and goes away with it.
import { useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent } from 'react';
import { ChevronLeft, ChevronRight, FileText, KeyRound, RefreshCw, X } from 'lucide-react';
import type { FinanceAccount, FinanceImport, FinancePdfRole, FinancePdfStatement } from '@anum/contracts';
import * as api from './lib/financeApi';
import { upload as uploadToVault, duplicateOf } from './lib/vaultApi';
import { describeError } from './lib/errors';
import { formatAmount, rangeLabel } from './lib/finance';
import {
  PDF_PAGE_SIZE, PDF_ROLES, checkRoles, filterRows, flagLabel, importBody, liveCheck, pdfErrorCode, reviewRows, roleLabel, rowProblem, type ReviewRow,
} from './lib/financePdf';
import { t } from './lib/i18n';
import { FormError, Notice } from './FinanceParts';

type Phase = { kind: 'reading' } | { kind: 'password'; wrong: boolean } | { kind: 'error'; message: string } | { kind: 'ready' };

export interface PdfImported { result: FinanceImport; vault: string | null }

const DATE_CHOICES = ['%d/%m/%Y', '%m/%d/%Y', '%Y-%m-%d', '%d-%m-%Y', '%d.%m.%Y', '%d %b %Y', '%d-%b-%Y'];

export function PdfStatementImport({ file, account, onImported, onCancel }: {
  file: File; account: FinanceAccount; onImported: (done: PdfImported) => void; onCancel: () => void;
}) {
  const [phase, setPhase] = useState<Phase>({ kind: 'reading' });
  const [password, setPassword] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [view, setView] = useState<FinancePdfStatement | null>(null);
  const [roles, setRoles] = useState<FinancePdfRole[]>([]);
  const [dateFormat, setDateFormat] = useState('');
  const [rows, setRows] = useState<ReviewRow[]>([]);
  const [opening, setOpening] = useState(''), [closing, setClosing] = useState('');
  const [query, setQuery] = useState(''), [offset, setOffset] = useState(0);
  const [accept, setAccept] = useState(false), [keep, setKeep] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const passwordId = useId(), errorId = useId(), searchId = useId(), headId = useId(), checkId = useId();
  const reviewRef = useRef<HTMLHeadingElement>(null);
  const currency = account.currency;

  const read = useCallback(async (options: { roles?: FinancePdfRole[] | null; dateFormat?: string; secret?: string } = {}) => {
    setBusy(true); setError(null);
    if (!view) setPhase({ kind: 'reading' });
    try {
      const result = await api.readPdfStatement(file, { account_id: account.id, filename: file.name, roles: options.roles ?? null, date_format: options.dateFormat || null }, options.secret || undefined);
      setView(result); setRoles(result.roles); setDateFormat(options.dateFormat ?? '');
      setRows(reviewRows(result)); setOpening(result.check.opening ?? ''); setClosing(result.check.closing ?? '');
      setOffset(0); setAccept(false); setPasswordError(null); setPhase({ kind: 'ready' });
      requestAnimationFrame(() => reviewRef.current?.focus());
    } catch (e) {
      const code = pdfErrorCode(e);
      if (code === 'password_required' || code === 'password_wrong') {
        setView(null); setPhase({ kind: 'password', wrong: code === 'password_wrong' });
        if (code === 'password_wrong') setPasswordError(describeError(e));
      } else if (view) setError(describeError(e));
      else setPhase({ kind: 'error', message: describeError(e) });
    } finally { setBusy(false); }
  }, [account.id, file, view]);

  // Read on open and again when the account changes (duplicates are checked against that account).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void read({ roles: null, secret: password }); }, [file, account.id]);

  function openWithPassword(event: FormEvent) {
    event.preventDefault();
    if (!password) { setPasswordError(t('fpdf.password.empty')); return; }
    void read({ roles: null, secret: password });
  }

  function reread(event?: FormEvent) {
    event?.preventDefault();
    if (view?.layout === 'table') {
      const problem = checkRoles(roles);
      if (problem) { setError(problem); return; }
    }
    void read({ roles: view?.layout === 'table' ? roles : null, dateFormat, secret: password });
  }

  const check = useMemo(() => liveCheck(rows, opening, closing, currency), [rows, opening, closing, currency]);
  const problems = useMemo(() => rows.filter((row) => row.include && rowProblem(row, currency) !== null).length, [rows, currency]);
  const shown = useMemo(() => filterRows(rows, query), [rows, query]);
  const pageRows = shown.slice(offset, offset + PDF_PAGE_SIZE);
  const included = rows.filter((row) => row.include);
  const left = rows.length - included.length;
  const fresh = included.filter((row) => !row.duplicate).length;

  function change(key: string, patch: Partial<ReviewRow>) {
    // Editing an amount is the owner's answer to a guessed sign.
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch, flags: patch.amount !== undefined ? row.flags.filter((f) => f !== 'sign_unsure') : row.flags } : row)));
  }

  async function confirm() {
    if (!view) return;
    setBusy(true); setError(null);
    try {
      const result = await api.importPdfStatement(importBody(view, account.id, rows, opening, closing, check.status === 'mismatch' && accept));
      let vault: string | null = null;
      if (keep) {
        try {
          await uploadToVault(file, { title: file.name.replace(/\.pdf$/i, ''), category: 'bank_statement', access_class: 'private' });
          vault = t('fpdf.saved');
        } catch (e) {
          vault = duplicateOf(e) ? t('fpdf.savedAlready') : t('fpdf.saveFailed', { message: describeError(e) });
        }
      }
      setPassword('');
      onImported({ result, vault });
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }

  if (phase.kind === 'reading') return <section className="surface cmPanel" aria-label={file.name}><Notice busy>{t('fpdf.reading')}</Notice></section>;
  if (phase.kind === 'error') return <section className="surface cmPanel" aria-labelledby={headId}>
    <h2 id={headId}>{t('fpdf.error.title')}</h2>
    <Notice error>{phase.message}</Notice>
    <div className="actions">
      <button type="button" onClick={() => void read({ roles: null, secret: password })}><RefreshCw aria-hidden="true" />{t('fpdf.error.again')}</button>
      <button type="button" className="secondary" onClick={onCancel}><X aria-hidden="true" />{t('fpdf.cancel')}</button>
    </div>
  </section>;
  if (phase.kind === 'password') return <section className="surface cmPanel" aria-labelledby={headId}>
    <h2 id={headId}><KeyRound aria-hidden="true" className="cmInlineIcon" />{t('fpdf.password.title')}</h2>
    <p className="muted">{t('fpdf.password.text')}</p>
    <form className="finForm" onSubmit={openWithPassword} aria-describedby={passwordError ? errorId : undefined}>
      <label className="field" htmlFor={passwordId}><span>{t('fpdf.password.label')}</span>
        <input id={passwordId} type="password" autoComplete="off" spellCheck={false} value={password} maxLength={128}
          onChange={(e) => { setPassword(e.target.value); setPasswordError(null); }} aria-invalid={passwordError ? true : undefined} /></label>
      <FormError id={errorId} text={passwordError} />
      <div className="actions">
        <button type="submit" disabled={busy}>{t('fpdf.password.open')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={onCancel}>{t('fpdf.cancel')}</button>
      </div>
    </form>
  </section>;
  if (!view) return null;

  const money = (value: string | null) => (value ? formatAmount(value, currency) : '-');
  const ambiguous = view.date_format_ambiguous && !dateFormat;
  return <section className="surface cmPanel finPdf" aria-labelledby={headId}>
    <h2 id={headId} ref={reviewRef} tabIndex={-1}><FileText aria-hidden="true" className="cmInlineIcon" />{t('fpdf.review.title', { file: view.filename })}</h2>
    <p className="muted">{t('fpdf.review.intro')} {t('fpdf.privacy')}</p>
    <dl className="cmFacts">
      <div><dt>{t('fpdf.summary.source')}</dt><dd>{t(view.source === 'ocr' ? 'fpdf.summary.source.ocr' : 'fpdf.summary.source.text')} · {t('fpdf.summary.pages', { count: view.page_count })}</dd></div>
      <div><dt>{t('fpdf.summary.period')}</dt><dd>{view.period_start && view.period_end ? <bdi dir="ltr">{t('fpdf.summary.periodValue', { start: view.period_start, end: view.period_end })}</bdi> : '-'}</dd></div>
      <div><dt>{t('fpdf.summary.account')}</dt><dd>{view.account_last4 ? t('fpdf.summary.ending', { last4: view.account_last4 }) : '-'}</dd></div>
      <div><dt>{t('fpdf.summary.rows')}</dt><dd>{t('fpdf.summary.rowsValue', { count: view.row_count, duplicates: view.duplicate_count })}</dd></div>
    </dl>
    {view.account_last4_matches === false && <Notice error>{t('fpdf.warning.last4', { statement: view.account_last4 ?? '-', account: account.name, own: account.last4 ?? '-' })}</Notice>}
    {view.warnings.includes('currency_differs') && <Notice error>{t('fpdf.warning.currency', { statement: view.statement_currency ?? '-', account: account.name, own: currency })}</Notice>}
    {view.warnings.includes('no_rows') && <Notice error>{t('fpdf.warning.noRows')}</Notice>}
    {ambiguous && <Notice>{t('fpdf.warning.ambiguous', { format: view.date_format })}</Notice>}

    <form onSubmit={reread}><fieldset className="finMapping">
      <legend>{t('fpdf.mapping.title')}</legend>
      <p className="muted">{view.layout === 'table' ? t('fpdf.mapping.help') : t('fpdf.mapping.lines')}</p>
      <div className="cmFilters">
        {view.layout === 'table' && view.columns.map((column, index) => <label key={column.index} className="cmInline"><span dir="auto">{column.label}</span>
          <select aria-label={t('fpdf.mapping.column', { label: column.label })} value={roles[index] ?? 'skip'} onChange={(e) => setRoles((current) => current.map((role, i) => (i === index ? e.target.value as FinancePdfRole : role)))}>
            {PDF_ROLES.map((role) => <option key={role} value={role}>{roleLabel(role)}</option>)}
          </select></label>)}
        <label className="cmInline"><span>{t('fpdf.dateFormat')}</span>
          <select value={dateFormat} onChange={(e) => setDateFormat(e.target.value)}>
            <option value="">{t('finance.import.detect')} ({view.date_format})</option>
            {[...new Set([...view.date_formats, ...DATE_CHOICES])].map((f) => <option key={f} value={f}>{f}</option>)}
          </select></label>
        <button type="submit" className="secondary" disabled={busy}><RefreshCw aria-hidden="true" />{t('fpdf.mapping.reread')}</button>
      </div>
    </fieldset></form>

    <section className="finPdfCheck" aria-labelledby={checkId} data-status={check.status}>
      <h3 id={checkId}>{t('fpdf.check.title')}</h3>
      <div className="cmFormGrid">
        <label className="field"><span>{t('fpdf.summary.opening')} ({currency})</span><input inputMode="decimal" dir="ltr" value={opening} onChange={(e) => setOpening(e.target.value)} />
          {view.check.opening_source === 'rows' && <small className="muted">{t('fpdf.summary.fromRows')}</small>}</label>
        <label className="field"><span>{t('fpdf.summary.closing')} ({currency})</span><input inputMode="decimal" dir="ltr" value={closing} onChange={(e) => setClosing(e.target.value)} />
          {view.check.closing_source === 'rows' && <small className="muted">{t('fpdf.summary.fromRows')}</small>}</label>
      </div>
      <div aria-live="polite">
        {check.status === 'matched' && <Notice>{t('fpdf.check.matched', { opening: money(opening), count: check.count, total: money(check.total), closing: money(closing) })}</Notice>}
        {check.status === 'mismatch' && <Notice error>{t('fpdf.check.mismatch', { opening: money(opening), total: money(check.total), expected: money(check.expected), closing: money(closing), difference: money(check.difference) })}
          {check.breaks.length > 0 && <> {t('fpdf.check.breaks', { lines: check.breaks.slice(0, 5).join(', ') })}</>}</Notice>}
        {check.status === 'unknown' && <Notice>{t('fpdf.check.unknown')}</Notice>}
      </div>
    </section>

    <div className="cmFilters">
      <label className="cmInline cmSearch" htmlFor={searchId}><span>{t('fpdf.rows.search')}</span>
        <input id={searchId} type="search" value={query} onChange={(e) => { setQuery(e.target.value); setOffset(0); }} /></label>
    </div>
    {shown.length === 0 ? <p className="muted">{rows.length ? t('fpdf.rows.noMatch') : t('fpdf.warning.noRows')}</p> : <div className="cmTableWrap" tabIndex={0}>
      <table className="cmTable finTable finPdfTable">
        <caption className="visuallyHidden">{t('fpdf.rows.caption')}</caption>
        <thead><tr>
          <th scope="col">{t('fpdf.col.include')}</th><th scope="col">{t('finance.col.date')}</th><th scope="col">{t('finance.col.description')}</th>
          <th scope="col" className="num">{t('finance.col.amount')} ({currency})</th><th scope="col" className="num">{t('fpdf.role.balance')}</th><th scope="col">{t('fpdf.col.notes')}</th>
        </tr></thead>
        <tbody>{pageRows.map((row) => {
          const problem = row.include ? rowProblem(row, currency) : null;
          const broken = check.breaks.includes(row.line);
          const classes = [row.duplicate ? 'finDuplicate' : '', row.include ? '' : 'finPdfLeft', problem || broken ? 'finPdfProblem' : ''].filter(Boolean).join(' ');
          return <tr key={row.key} className={classes || undefined}>
            <td><input type="checkbox" className="finPdfTick" checked={row.include} aria-label={t('fpdf.rows.includeRow', { line: row.line })} onChange={(e) => change(row.key, { include: e.target.checked })} /></td>
            <td><input type="date" className="finPdfDate" value={row.posted_on} aria-label={t('fpdf.rows.date', { line: row.line })} onChange={(e) => change(row.key, { posted_on: e.target.value })} /></td>
            <td className="finDescription"><input className="finPdfText" dir="auto" value={row.description} maxLength={500} aria-label={t('fpdf.rows.description', { line: row.line })} onChange={(e) => change(row.key, { description: e.target.value })} /></td>
            <td className="num"><input className="finPdfAmount" dir="ltr" inputMode="decimal" value={row.amount} aria-label={t('fpdf.rows.amount', { line: row.line })} aria-invalid={problem ? true : undefined} onChange={(e) => change(row.key, { amount: e.target.value })} /></td>
            <td className="num"><bdi dir="ltr">{money(row.balance)}</bdi></td>
            <td>
              <small>{t('fpdf.rows.page', { page: row.page })}{row.reference ? ` · ${t('fpdf.rows.reference', { reference: row.reference })}` : ''}</small>
              {row.duplicate && <small>{t('fpdf.rows.duplicate')}</small>}
              {row.flags.map((flag) => flagLabel(flag)).filter(Boolean).map((label) => <small key={label} className="finPdfFlag">{label}</small>)}
              {problem && <small className="cmFieldError">{problem}</small>}
            </td>
          </tr>;
        })}</tbody>
      </table>
    </div>}
    {shown.length > PDF_PAGE_SIZE && <nav className="cmPager" aria-label={t('fpdf.rows.caption')}>
      <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PDF_PAGE_SIZE))}><ChevronLeft aria-hidden="true" className="cmFlip" />{t('fpdf.rows.previous')}</button>
      <span className="muted" aria-live="polite">{rangeLabel(offset, pageRows.length, shown.length)}</span>
      <button type="button" className="secondary" disabled={offset + PDF_PAGE_SIZE >= shown.length} onClick={() => setOffset(offset + PDF_PAGE_SIZE)}>{t('fpdf.rows.next')}<ChevronRight aria-hidden="true" className="cmFlip" /></button>
    </nav>}
    {left > 0 && <p className="muted">{t('fpdf.rows.left', { count: left })}</p>}
    {problems > 0 && <p className="cmFieldError" role="status">{t('fpdf.rows.problems', { count: problems })}</p>}

    {check.status === 'mismatch' && <label className="finCheck"><input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} />{t('fpdf.accept')}</label>}
    <label className="finCheck"><input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />{t('fpdf.saveVault')}</label>
    <FormError id={errorId} text={error} />
    <div className="actions">
      <button type="button" disabled={busy || fresh === 0 || problems > 0 || (check.status === 'mismatch' && !accept)} onClick={() => void confirm()}>
        {busy ? t('finance.import.importing') : t('finance.import.confirm', { count: fresh })}</button>
      <button type="button" className="secondary" disabled={busy} onClick={onCancel}><X aria-hidden="true" />{t('fpdf.cancel')}</button>
    </div>
    {fresh === 0 && included.length > 0 && <p className="muted">{t('finance.import.nothingNew')}</p>}
  </section>;
}
