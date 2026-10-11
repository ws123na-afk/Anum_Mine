// Developer mode (docs/developer-mode.md): the owner asks for a change, a coding agent builds it in
// GitHub Actions and opens a draft pull request, and the merge waits for the owner's approval with
// the payload hash shown here. Owners only; everyone else sees what it is and who can use it.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { CheckCircle2, CircleDot, ExternalLink, Fingerprint, GitPullRequest, Globe, Hammer, Lock, RefreshCw, Search, Send, ShieldCheck, XCircle } from 'lucide-react';
import type { Approval } from '@anum/contracts';
import * as api from './lib/api';
import { argumentRows, expiryLabel, shortHash } from './lib/approvals';
import {
  EXAMPLE_REQUESTS, MAX_REQUEST_CHARS, STATUS_LABEL, canCancel, canRequestMerge, checksLabel, pollDelay, requestBody, setupSteps, shortText, statusTone,
  type DevRequest, type DevSetup,
} from './lib/developer';
import { cancelDeveloperRequest, createDeveloperRequest, getDeveloperRequest, getDeveloperSetup, listDeveloperRequests, pendingApproval, requestMergeApproval } from './lib/developerApi';
import { describeError, isBudgetExceeded, isPermissionDenied, budgetMessage } from './lib/errors';
import { useIsOwner } from './useIsOwner';
import './developer.css';

const PAGE = 20;
const formatDate = (value: string) => new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));

export function DeveloperView({ onOpenBudgets }: { onOpenBudgets?: () => void } = {}) {
  const owner = useIsOwner();
  if (owner === null) return <p className="notice" role="status"><CircleDot aria-hidden="true" />Checking your role...</p>;
  if (!owner) return <OwnerOnly />;
  return <DeveloperWorkspace onOpenBudgets={onOpenBudgets} />;
}

function OwnerOnly() {
  return <section className="surface devPanel" aria-labelledby="dev-owner-only">
    <Lock aria-hidden="true" className="devPanelIcon" />
    <div>
      <h2 id="dev-owner-only">Developer mode is for workspace owners</h2>
      <p className="muted">An owner can ask ANUM's developer to change the app itself: it writes the change on a branch, runs CI and opens a pull request, and nothing merges until an owner approves it. Ask an owner of this workspace if you want something built.</p>
    </div>
  </section>;
}

function DeveloperWorkspace({ onOpenBudgets }: { onOpenBudgets?: () => void }) {
  const [setup, setSetup] = useState<DevSetup | null>(null);
  const [items, setItems] = useState<DevRequest[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [budget, setBudget] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [allowProtected, setAllowProtected] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [appliedQuery, setAppliedQuery] = useState('');
  const [more, setMore] = useState(false);
  const [reviews, setReviews] = useState<Record<string, Approval>>({});
  // "Not now" hides a pending merge approval here; it stays in Approvals until it lapses.
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  const input = useRef<HTMLTextAreaElement | null>(null);
  const draftId = useId();

  const load = useCallback(async (search = appliedQuery) => {
    try {
      const [s, list] = await Promise.all([getDeveloperSetup(), listDeveloperRequests({ limit: PAGE, query: search })]);
      setSetup(s);
      setItems(list);
      setMore(list.length === PAGE);
      setState('ready');
      setError(null);
    } catch (e) {
      setState('error');
      setError(isPermissionDenied(e) ? 'Only workspace owners can use developer mode.' : `Developer requests could not be loaded: ${describeError(e)}`);
    }
  }, [appliedQuery]);

  useEffect(() => { void load(); }, [load]);

  // Refresh while something is moving (queued, coding, CI); the API reads the progress from GitHub.
  useEffect(() => {
    const delay = pollDelay(items);
    if (delay === null || state !== 'ready') return;
    const timer = window.setTimeout(() => { void load(); }, delay);
    return () => window.clearTimeout(timer);
  }, [items, state, load]);

  const replace = (next: DevRequest) => setItems((current) => current.map((item) => item.id === next.id ? next : item));

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = requestBody(draft, allowProtected);
    if (!body.ok) { setNotice(body.error); return; }
    setBusy('create');
    setNotice(null);
    setBudget(null);
    try {
      const created = await createDeveloperRequest(body.value, 'text');
      setItems((current) => [created, ...current]);
      setDraft('');
      setAllowProtected(false);
      setNotice('Sent to the developer. Progress appears below; the pull request opens when the change and its checks are done.');
      setSetup((current) => current ? { ...current, requests_today: current.requests_today + 1 } : current);
    } catch (e) {
      if (isBudgetExceeded(e)) setBudget(budgetMessage(e));
      else setNotice(`Not sent: ${describeError(e)}`);
    } finally { setBusy(null); }
  }

  async function act(id: string, kind: 'refresh' | 'cancel' | 'review', fn: () => Promise<void>) {
    setBusy(`${kind}:${id}`);
    setNotice(null);
    try { await fn(); } catch (e) { setNotice(describeError(e)); } finally { setBusy(null); }
  }

  const refresh = (item: DevRequest) => act(item.id, 'refresh', async () => replace(await getDeveloperRequest(item.id, true)));
  const cancel = (item: DevRequest) => act(item.id, 'cancel', async () => {
    if (!confirm(`Cancel "${shortText(item.text, 60)}"? The coding run stops, the pull request closes and its branch is deleted.`)) return;
    replace(await cancelDeveloperRequest(item.id));
  });
  const review = (item: DevRequest) => act(item.id, 'review', async () => {
    const result = await requestMergeApproval(item.id);
    replace(result.request);
    setDismissed((current) => { const next = new Set(current); next.delete(item.id); return next; });
    setReviews((current) => ({ ...current, [item.id]: result.approval }));
  });
  async function merge(item: DevRequest, approval: Approval) {
    setBusy(`merge:${item.id}`);
    setNotice(null);
    try {
      const result = await api.approveTask(approval);
      setReviews((current) => { const next = { ...current }; delete next[item.id]; return next; });
      const updated = await getDeveloperRequest(item.id, true);
      replace(updated);
      setNotice(updated.status === 'merged' ? `Merged pull request #${updated.pr_number}.` : (result.run?.result ?? updated.merge_error ?? 'The merge did not happen.'));
    } catch (e) {
      setNotice(isPermissionDenied(e) ? `Merge refused: ${describeError(e)}` : `Not merged: ${describeError(e)}`);
    } finally { setBusy(null); }
  }

  async function loadMore() {
    const last = items[items.length - 1];
    if (!last) return;
    setBusy('more');
    try {
      const page = await listDeveloperRequests({ limit: PAGE, before: last.id, query: appliedQuery });
      setItems((current) => [...current, ...page]);
      setMore(page.length === PAGE);
    } catch (e) { setNotice(`More requests could not be loaded: ${describeError(e)}`); } finally { setBusy(null); }
  }

  const ready = Boolean(setup?.configured && setup.enabled_here);
  const limitReached = Boolean(setup && setup.requests_today >= setup.max_requests_per_day);
  const activeExists = items.some((item) => item.status === 'queued' || item.status === 'coding');

  return <div className="devLayout">
    <section className="moduleIntro">
      <div>
        <p className="eyebrow">Build ANUM itself</p>
        <h2>Ask for a change</h2>
        <p>Describe a change. A coding agent makes it on a branch in GitHub, runs CI and opens a draft pull request. Nothing merges until you approve it here.</p>
      </div>
      <button type="button" className="secondary" onClick={() => void load()} disabled={state === 'loading'}><RefreshCw aria-hidden="true" />Refresh requests</button>
    </section>

    {state === 'loading' && <p className="notice" role="status"><CircleDot aria-hidden="true" />Loading developer requests...</p>}
    {state === 'error' && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{error}</span><span className="actions devInline"><button type="button" className="secondary" onClick={() => void load()}>Try again</button></span></div>}

    {setup && !ready && <section className="surface devPanel" aria-labelledby="dev-setup">
      <Hammer aria-hidden="true" className="devPanelIcon" />
      <div>
        <h2 id="dev-setup">Set up developer mode</h2>
        <p className="muted">{setup.message}</p>
        {setup.missing.length > 0 && <>
          <p className="devSetupLead">The person who deploys the ANUM API adds these settings (names only; never paste values in chat):</p>
          <ul className="devSetupList">{setupSteps(setup).map((step) => <li key={step}><code>{step.split(':')[0]}</code>{step.slice(step.indexOf(':'))}</li>)}</ul>
          <p className="muted">In the GitHub repository, add the secrets <code>ANTHROPIC_API_KEY</code> and <code>ANUM_DEV_PUSH_TOKEN</code> under Settings → Secrets and variables → Actions. The full steps are in docs/developer-mode.md.</p>
        </>}
      </div>
    </section>}

    {ready && setup && <form className="surface devForm" onSubmit={(e) => void submit(e)} aria-label="New developer request">
      <label className="field" htmlFor={draftId}>
        <span>What should change?</span>
        <textarea id={draftId} ref={input} value={draft} maxLength={MAX_REQUEST_CHARS} rows={3} placeholder="For example: add a dark/light toggle to Settings" onChange={(e) => setDraft(e.target.value)} />
      </label>
      <div className="actions devFormFooter">
        <label className="devCheck"><input type="checkbox" checked={allowProtected} onChange={(e) => setAllowProtected(e.target.checked)} /><span>Allow changes to workflows and secret handling</span></label>
        <small className="muted">{setup.requests_today} of {setup.max_requests_per_day} requests used today · builds {setup.repository} from {setup.base_branch}</small>
        <button type="submit" disabled={busy === 'create' || !draft.trim() || limitReached || activeExists}><Send aria-hidden="true" />{busy === 'create' ? 'Sending...' : 'Ask the developer'}</button>
      </div>
      {activeExists && <p className="muted devHint">One request at a time: wait until the current one opens its pull request, or cancel it.</p>}
      {limitReached && <p className="muted devHint">Today's request limit is reached. Try again tomorrow.</p>}
    </form>}

    {budget && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{budget}</span>{onOpenBudgets && <span className="actions devInline"><button type="button" className="secondary" onClick={onOpenBudgets}>Open Budgets</button></span>}</p>}
    {notice && <p className="notice" role="status" aria-live="polite"><CircleDot aria-hidden="true" />{notice}</p>}

    {state === 'ready' && (items.length > 0 || appliedQuery) && <form className="actions devSearch" role="search" onSubmit={(e) => { e.preventDefault(); setAppliedQuery(query); }}>
      <label className="visuallyHidden" htmlFor={`${draftId}-search`}>Search requests</label>
      <input id={`${draftId}-search`} type="search" value={query} placeholder="Search requests" onChange={(e) => setQuery(e.target.value)} />
      <button type="submit" className="secondary"><Search aria-hidden="true" />Search</button>
      {appliedQuery && <button type="button" className="secondary" onClick={() => { setQuery(''); setAppliedQuery(''); }}>Clear</button>}
    </form>}

    {state === 'ready' && items.length === 0 && <section className="surface emptyState">
      <Hammer aria-hidden="true" />
      <h3>{appliedQuery ? 'No matching requests' : 'No developer requests yet'}</h3>
      <p>{appliedQuery ? 'Try other words, or clear the search.' : ready ? 'Ask for a change above, say "developer, ..." in Voice, or start a chat message with "build". For example:' : 'Once developer mode is set up, ask for a change here, in the Assistant or by voice.'}</p>
      {ready && !appliedQuery && <div className="actions devExamples">{EXAMPLE_REQUESTS.map((example) => <button type="button" className="secondary" key={example} onClick={() => { setDraft(example); input.current?.focus(); }}>{example}</button>)}</div>}
    </section>}

    {items.length > 0 && <ol className="devList" aria-label="Developer requests">
      {items.map((item) => <li key={item.id}>
        <RequestCard
          item={item}
          busy={busy}
          review={dismissed.has(item.id) ? null : reviews[item.id] ?? pendingApproval(item)}
          onRefresh={() => void refresh(item)}
          onCancel={() => void cancel(item)}
          onReview={() => void review(item)}
          onMerge={(approval) => void merge(item, approval)}
          onDismissReview={() => setDismissed((current) => new Set(current).add(item.id))}
        />
      </li>)}
    </ol>}
    {more && <div className="actions devMore"><button type="button" className="secondary" onClick={() => void loadMore()} disabled={busy === 'more'}>{busy === 'more' ? 'Loading...' : 'Load older requests'}</button></div>}
  </div>;
}

function RequestCard({ item, busy, review, onRefresh, onCancel, onReview, onMerge, onDismissReview }: {
  item: DevRequest;
  busy: string | null;
  review: Approval | null;
  onRefresh: () => void;
  onCancel: () => void;
  onReview: () => void;
  onMerge: (approval: Approval) => void;
  onDismissReview: () => void;
}) {
  const title = item.pr_title ?? shortText(item.text);
  return <article className="surface devCard" aria-label={`Developer request: ${shortText(item.text, 60)}`}>
    <header className="devCardHeader">
      <div>
        <h3>{title}</h3>
        <small className="muted">{formatDate(item.created_at)} · {item.source === 'voice' ? 'spoken' : 'typed'} · <code>{item.branch}</code></small>
      </div>
      <span className={`statusBadge ${statusTone(item.status)}`}>{STATUS_LABEL[item.status]}</span>
    </header>
    {item.pr_title && <p className="devRequestText">{item.text}</p>}
    <ol className="devTimeline" aria-label="Progress">
      {item.timeline.map((stage) => <li key={stage.id} className={`devStage ${stage.state}`} aria-current={stage.state === 'active' ? 'step' : undefined}>
        <span className="devStageDot" aria-hidden="true" />
        <span>{stage.label}<span className="visuallyHidden">: {stage.state}</span></span>
      </li>)}
    </ol>
    <p className="devSummary" role="status">{item.summary}</p>
    {item.pr_number !== null && <p className="muted devChecks">{checksLabel(item.checks)}</p>}
    {item.status === 'failed' && item.failure_reason && <p className="devProblem">{item.failure_reason}</p>}
    {item.merge_error && item.status !== 'merged' && <p className="devProblem">{item.merge_error}</p>}
    <div className="devLinks">
      {item.pr_url && <a href={item.pr_url} target="_blank" rel="noreferrer"><GitPullRequest aria-hidden="true" />Pull request #{item.pr_number}<ExternalLink aria-hidden="true" className="devExternal" /></a>}
      {item.run_url && <a href={item.run_url} target="_blank" rel="noreferrer"><Hammer aria-hidden="true" />Coding run<ExternalLink aria-hidden="true" className="devExternal" /></a>}
    </div>
    {review && item.status === 'waiting_approval' && <MergeReview approval={review} busy={busy === `merge:${item.id}`} onMerge={() => onMerge(review)} onDismiss={onDismissReview} />}
    <div className="actions devActions">
      {canRequestMerge(item) && !review && <button type="button" onClick={onReview} disabled={busy !== null}><ShieldCheck aria-hidden="true" />{busy === `review:${item.id}` ? 'Preparing...' : 'Approve & merge'}</button>}
      {item.status !== 'merged' && item.status !== 'cancelled' && item.status !== 'failed' && <button type="button" className="secondary" onClick={onRefresh} disabled={busy !== null}><RefreshCw aria-hidden="true" />{busy === `refresh:${item.id}` ? 'Checking...' : 'Check now'}</button>}
      {canCancel(item) && <button type="button" className="danger" onClick={onCancel} disabled={busy !== null}><XCircle aria-hidden="true" />Cancel</button>}
    </div>
  </article>;
}

/** The explicit approval step: exactly what will run, bound to the payload hash shown here. */
function MergeReview({ approval, busy, onMerge, onDismiss }: { approval: Approval; busy: boolean; onMerge: () => void; onDismiss: () => void }) {
  const rows = argumentRows(approval.arguments);
  const expiry = expiryLabel(approval);
  return <section className="devReview" aria-label="Merge approval">
    <h4><ShieldCheck aria-hidden="true" />Approve the merge</h4>
    <p className="muted">{approval.reason}</p>
    {approval.target && <small className="approvalTarget"><Globe aria-hidden size={12} /> Sends to <code>{approval.target}</code></small>}
    <dl className="approvalArgs">{rows.map((row) => <div className="approvalArg" key={row.key}><dt>{row.key}</dt><dd><code>{row.value}</code></dd></div>)}</dl>
    <small className="approvalHash" title={approval.payloadHash ?? undefined}><Fingerprint aria-hidden size={12} /> Payload hash <code>{shortHash(approval.payloadHash)}</code>{expiry && <> · {expiry}</>}</small>
    <p className="muted">Merging also needs every CI check on this commit to pass at the moment you approve. Workspace approval rules (for example two people) still apply.</p>
    <div className="actions">
      <button type="button" onClick={onMerge} disabled={busy || !approval.payloadHash}><CheckCircle2 aria-hidden="true" />{busy ? 'Merging...' : 'Confirm merge'}</button>
      <button type="button" className="secondary" onClick={onDismiss} disabled={busy}>Not now</button>
    </div>
  </section>;
}
