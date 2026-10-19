// Approvals: the exact tool call, where it goes, why it stopped, when it lapses, and who decided
// and why. See docs/approvals-and-risk.md. Approve sends back the payload hash shown here.
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { BellOff, CheckCircle2, Clock, Fingerprint, Globe, Route, Search, ShieldCheck, UserX, Users, XCircle } from 'lucide-react';
import type { Approval } from '@anum/contracts';
import * as api from './lib/api';
import { APPROVAL_FILTERS, approvalSignal,APPROVAL_PAGE_SIZE, APPROVAL_SEARCH_DELAY_MS, emptyKind, mergeApprovals, searchText, splitApprovals, summaryTotal } from './lib/approvalPaging';
import { REASON_MAX_CHARS, approvalProgress, argumentRows, canApprove, decisionSummary, effectiveStatus, expiryLabel, hasApproved, joinNames, normalizeReason, shortHash } from './lib/approvals';
import { mayApprove, mayReject, routingView, suppressedText } from './lib/approvalRules';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, formatNumber, t } from './lib/i18n';
import { riskLabel, statusLabel } from './lib/labels';
import { EmptyPanel, PanelDenied, PanelError, PanelLoading } from './Panels';
import { rich } from './rich';

/**
 * Resolves with the API's refusal when the decision is not allowed (403, for example the
 * two-person rule refusing to let you approve your own high-risk task), otherwise with null.
 */
type Decide = (approval: Approval, decision: 'approve' | 'reject', reason?: string) => Promise<string | null>;

const formatDate = (value: string) => formatDateTime(value);

type Load = { state: 'loading' } | { state: 'ready' } | { state: 'denied' } | { state: 'error'; reason: string };

/**
 * The approval queue and its history, one keyset page at a time (docs/approvals-and-risk.md#paging-filters-and-search).
 * `items` is the shell's pending snapshot: it is only the signal to reload the first page when an approval
 * event arrives (the list itself is fetched here, filtered and searched on the server).
 */
export function ApprovalsView({ items, busy, decide, metric, empty, userId, onOpenRules }: {
  items: Approval[];
  busy: boolean;
  decide: Decide;
  metric(icon: ReactNode, label: string, value: string, detail: string): ReactNode;
  empty: ReactNode;
  /** The signed-in user, so a chain they already approved offers no second approval. */
  userId?: string | null;
  /** Opens the approval rules (Governance), where AED bands and approvers are set. */
  onOpenRules?: () => void;
}) {
  const [filter, setFilter] = useState<api.ApprovalFilter>('all');
  const [typed, setTyped] = useState('');
  const [query, setQuery] = useState('');
  const [rows, setRows] = useState<Approval[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [summary, setSummary] = useState<api.ApprovalSummary | null>(null);
  const [paging, setPaging] = useState(false);
  const [pagingError, setPagingError] = useState<string | null>(null);
  const request = useRef(0);
  const searchId = useId();
  const signal = approvalSignal(items);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(searchText(typed)), APPROVAL_SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [typed]);

  const fetchFirst = useCallback(async (quiet: boolean) => {
    const mine = ++request.current;
    if (!quiet) setLoad({ state: 'loading' });
    try {
      const [page, counts] = await Promise.all([
        api.getApprovalPage({ status: filter, query, limit: APPROVAL_PAGE_SIZE }),
        api.getApprovalSummary().catch(() => null),
      ]);
      if (mine !== request.current) return;
      setRows(page.items);
      setCursor(page.nextCursor);
      setSummary(counts);
      setPagingError(null);
      setLoad({ state: 'ready' });
    } catch (error) {
      if (mine !== request.current) return;
      const failed: Load = isPermissionDenied(error) ? { state: 'denied' } : { state: 'error', reason: describeError(error) };
      // A background refresh that fails keeps the list on screen instead of replacing it with an error.
      setLoad((current) => (quiet && current.state === 'ready' ? current : failed));
    }
  }, [filter, query]);

  // A new filter or search starts a fresh list; an approval event refreshes the first page quietly.
  useEffect(() => { void fetchFirst(false); }, [fetchFirst]);
  const firstRun = useRef(true);
  useEffect(() => {
    if (firstRun.current) { firstRun.current = false; return; }
    void fetchFirst(true);
  }, [signal]); // eslint-disable-line react-hooks/exhaustive-deps

  async function loadMore() {
    if (!cursor || paging) return;
    const mine = request.current;
    setPaging(true);
    setPagingError(null);
    try {
      const page = await api.getApprovalPage({ status: filter, query, cursor, limit: APPROVAL_PAGE_SIZE });
      if (mine !== request.current) return;
      setRows((current) => mergeApprovals(current, page.items));
      setCursor(page.nextCursor);
    } catch (error) {
      if (mine === request.current) setPagingError(t('approvals.loadMoreError', { reason: describeError(error) }));
    } finally {
      setPaging(false);
    }
  }

  // A decision changes the card (progress, status) even when the pending list looks the same: reload the page.
  const decideAndRefresh: Decide = async (approval, decision, reason) => {
    const refusal = await decide(approval, decision, reason);
    void fetchFirst(true);
    return refusal;
  };
  const clearSearch = () => { setTyped(''); setQuery(''); };
  const now = new Date();
  const status = (a: Approval) => effectiveStatus(a, now);
  const { pending, history } = splitApprovals(rows, now);
  const kind = emptyKind(filter, query);
  const total = summaryTotal(summary, filter);
  const searching = query !== '' || typed.trim() !== '';
  const ready = load.state === 'ready';
  const noMatch = <EmptyPanel icon={<Search />} title={t('approvals.empty.searchTitle')} text={t('approvals.empty.searchText', { query })} action={t('common.clear')} onAction={clearSearch} />;
  return (
    <>
      <section className="metrics three">
        {metric(<ShieldCheck />, t('approvals.metric.pending'), summary ? formatNumber(summary.pending) : '-', t('approvals.metric.pendingDetail'))}
        {metric(<CheckCircle2 />, t('approvals.metric.approved'), summary ? formatNumber(summary.approved) : '-', t('approvals.metric.total'))}
        {metric(<XCircle />, t('approvals.metric.rejected'), summary ? formatNumber(summary.rejected) : '-', t('approvals.metric.total'))}
      </section>
      {onOpenRules && (
        <p className="approvalRulesLink">
          <Route aria-hidden size={14} />
          <span>{t('routing.rulesLink.text')}</span>{' '}
          <button type="button" className="linkButton" onClick={onOpenRules}>{t('routing.rulesLink.open')}</button>
        </p>
      )}
      <div className="approvalToolbar">
        <div className="segmented" role="group" aria-label={t('approvals.filter.label')}>
          {APPROVAL_FILTERS.map((value) => (
            <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>{t(`approvals.filter.${value}`)}</button>
          ))}
        </div>
        <label className="approvalSearch" htmlFor={searchId}>
          <span className="visuallyHidden">{t('approvals.search.label')}</span>
          <Search aria-hidden size={16} />
          <input id={searchId} type="search" value={typed} maxLength={200} autoComplete="off" spellCheck={false}
            placeholder={t('approvals.search.placeholder')} onChange={(event) => setTyped(event.target.value)} />
        </label>
        {searching && <button type="button" className="secondary" onClick={clearSearch}>{t('common.clear')}</button>}
      </div>
      {load.state === 'loading' && <section className="surface approvalTable" aria-busy="true"><PanelLoading label={t('approvals.loading')} /></section>}
      {load.state === 'denied' && <section className="surface approvalTable"><PanelDenied title={t('approvals.denied.title')} text={t('approvals.denied.text')} /></section>}
      {load.state === 'error' && <section className="surface approvalTable"><PanelError message={t('approvals.error.text', { reason: load.reason })} onRetry={() => void fetchFirst(false)} /></section>}
      {ready && filter !== 'decided' && (
        <section className="surface approvalTable" aria-labelledby="approvals-pending-heading">
          <h2 className="visuallyHidden" id="approvals-pending-heading">{t('approvals.pendingHeading')}</h2>
          {pending.map((a) => <PendingApproval key={a.id} approval={a} busy={busy} decide={decideAndRefresh} now={now} userId={userId} />)}
          {!pending.length && (kind === 'search' ? noMatch : empty)}
        </section>
      )}
      {ready && filter !== 'pending' && history.length > 0 && (
        <section className="surface approvalTable approvalHistory" aria-label={t('approvals.history')}>
          <p className="eyebrow">{t('approvals.historyEyebrow')}</p>
          <h2>{t('approvals.decisions')}</h2>
          {history.map((a) => (
            <details className="approvalHistoryRow" key={a.id}>
              <summary>
                <span className={`statusBadge ${status(a) === 'approved' ? 'success' : 'dangerTone'}`}>{statusLabel(status(a))}</span>
                <strong dir="ltr">{a.action}</strong>
                <small>{decisionSummary(a, formatDate, now)}</small>
              </summary>
              {a.decisionReason && <p className="approvalReason"><span>{t('approvals.reason')}</span> <bdi>{a.decisionReason}</bdi></p>}
              <ProgressLine approval={a} />
              {routingView(a) && <RoutingBlock route={routingView(a)!} />}
              <TargetLine approval={a} />
              <ArgumentList approval={a} />
              <HashLine approval={a} />
            </details>
          ))}
        </section>
      )}
      {ready && filter === 'decided' && history.length === 0 && (
        <section className="surface approvalTable">
          {kind === 'search' ? noMatch : <EmptyPanel icon={<ShieldCheck />} title={t('approvals.empty.decidedTitle')} text={t('approvals.empty.decidedText')} />}
        </section>
      )}
      {ready && rows.length > 0 && (
        <div className="approvalMore">
          <small role="status">{total === null ? t('approvals.shownUnknown', { shown: formatNumber(rows.length) }) : t('approvals.shown', { shown: formatNumber(rows.length), total: formatNumber(Math.max(total, rows.length)) })}</small>
          {pagingError && <p className="approvalWarning" role="alert">{pagingError}</p>}
          {cursor && <button type="button" className="secondary" onClick={() => void loadMore()} disabled={paging}>{paging ? t('common.loading') : t('approvals.loadMore')}</button>}
        </div>
      )}
    </>
  );
}

function PendingApproval({ approval, busy, decide, now, userId }: { approval: Approval; busy: boolean; decide: Decide; now: Date; userId?: string | null }) {
  const alreadyApproved = hasApproved(approval, userId);
  // The API says whether this person may decide (approval routing by AED amount); a person the
  // route does not list sees the item read-only, with the reason.
  const approvable = canApprove(approval, now) && !alreadyApproved && mayApprove(approval);
  const rejectable = mayReject(approval);
  const route = routingView(approval);
  const suppressed = suppressedText(approval);
  const progress = approvalProgress(approval);
  const expiry = expiryLabel(approval, now);
  const [reason, setReason] = useState('');
  const [refusal, setRefusal] = useState<string | null>(null);
  const reasonId = useId();
  const typed = normalizeReason(reason);
  const act = (decision: 'approve' | 'reject') => {
    setRefusal(null);
    void decide(approval, decision, typed).then(setRefusal);
  };
  return (
    <article className="approvalDetail" aria-label={t('approvals.cardLabel', { action: approval.action })}>
      <div className="approvalIcon" aria-hidden="true"><ShieldCheck /></div>
      <div className="approvalBody">
        <h3>{rich('approvals.wantsToCall', { action: <code dir="ltr">{approval.action}</code> })}</h3>
        <p dir="auto">{approval.reason}</p>
        <ProgressLine approval={approval} />
        {progress && alreadyApproved && (
          <p className="approvalProgressNote">{t('approvals.youApproved', { count: progress.required - progress.collected })}</p>
        )}
        {route && <RoutingBlock route={route} />}
        {route?.explanation && !alreadyApproved && <p className="approvalReadOnly" role="note"><UserX aria-hidden size={14} />{route.explanation}</p>}
        {suppressed && <p className="approvalWarning approvalSuppressed" role="note"><BellOff aria-hidden size={14} />{suppressed}</p>}
        <TargetLine approval={approval} />
        <p className="approvalSubhead">{t('approvals.exactly')}</p>
        <ArgumentList approval={approval} />
        <HashLine approval={approval} />
        <small className="approvalMeta">
          {t('approvals.meta', { risk: riskLabel(approval.riskLevel), task: approval.taskId, when: formatDate(approval.createdAt) })}
          {expiry && <> · <Clock aria-hidden size={12} /> {expiry}</>}
        </small>
        {!approval.payloadHash && <p className="approvalWarning">{t('approvals.notBoundWarning')}</p>}
        <label className="approvalReasonField" htmlFor={reasonId}>
          <span>{t('approvals.reasonLabel')}</span>
          <textarea
            id={reasonId}
            value={reason}
            maxLength={REASON_MAX_CHARS}
            rows={2}
            placeholder={t('approvals.reasonPlaceholder')}
            onChange={(event) => setReason(event.target.value)}
          />
          <small>{t('approvals.reasonCount', { used: reason.length, max: REASON_MAX_CHARS })}</small>
        </label>
        {refusal && (
          <div className="approvalRefusal" role="alert">
            <UserX aria-hidden />
            <div><strong>{t('approvals.refused')}</strong><p>{refusal}</p></div>
          </div>
        )}
      </div>
      <div className="approvalActions">
        <button disabled={busy || !approvable} onClick={() => act('approve')}><CheckCircle2 aria-hidden="true" />{t('approvals.approve')}</button>
        <button className="danger" disabled={busy || !rejectable} onClick={() => act('reject')}><XCircle aria-hidden="true" />{t('approvals.reject')}</button>
      </div>
    </article>
  );
}

/** Who may approve and why (approval routing by AED amount, docs/approvals-and-risk.md#approval-routing). */
function RoutingBlock({ route }: { route: NonNullable<ReturnType<typeof routingView>> }) {
  return (
    <section className="approvalRouting" aria-label={t('routing.title')}>
      <p className="approvalRoutingWho"><Route aria-hidden size={14} /><strong>{t('routing.title')}:</strong> <span>{route.who}</span>{route.step && <small> · {route.step}</small>}</p>
      {route.why.length > 0 && <ul className="approvalRoutingWhy" aria-label={t('routing.why')}>{route.why.map((line) => <li key={line} dir="auto">{line}</li>)}</ul>}
      {route.steps.length > 1 && <ol className="approvalRoutingSteps">
        {route.steps.map((step) => <li key={step.label} className={step.current ? 'current' : undefined}>
          <strong>{step.label}</strong> <span>{step.who}</span>
          <small>{step.approvedBy ? t('routing.approvedBy', { who: step.approvedBy }) : t('routing.waiting')}</small>
        </li>)}
      </ol>}
    </section>
  );
}

function ArgumentList({ approval }: { approval: Approval }) {
  const rows = argumentRows(approval.arguments);
  if (!rows.length) return <p className="muted">{t('approvals.noArguments')}</p>;
  return (
    <dl className="approvalArgs">
      {rows.map((row) => (
        <div className="approvalArg" key={row.key}>
          <dt><bdi>{row.key}</bdi></dt>
          <dd className={row.redacted ? 'redacted' : undefined}>
            {row.multiline ? <pre dir="auto">{row.value}</pre> : <code dir="auto">{row.value}</code>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** "1 of 3 approvals" and who approved, for approvals that need several people. */
function ProgressLine({ approval }: { approval: Approval }) {
  const progress = approvalProgress(approval);
  if (!progress) return null;
  return (
    <div className="approvalProgress" aria-label={t('approvals.progressLabel', { label: progress.label })}>
      <Users aria-hidden size={14} />
      <strong>{progress.label}</strong>
      <meter min={0} max={progress.required} value={progress.collected} aria-hidden />
      <small>{progress.approvers.length ? t('approvals.approvedBy', { names: joinNames(progress.approvers) }) : t('approvals.noneYet')}</small>
    </div>
  );
}

function TargetLine({ approval }: { approval: Approval }) {
  if (!approval.target) return null;
  return (
    <small className="approvalTarget">
      <Globe aria-hidden size={12} /> {t('approvals.sendsTo')} <code dir="ltr">{approval.target}</code>
    </small>
  );
}

function HashLine({ approval }: { approval: Approval }) {
  return (
    <small className="approvalHash" title={approval.payloadHash ?? undefined}>
      <Fingerprint aria-hidden size={12} /> {t('approvals.payloadHash')} <code dir="ltr">{shortHash(approval.payloadHash)}</code>
    </small>
  );
}
