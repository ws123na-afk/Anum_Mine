// Approvals: the exact tool call, where it goes, why it stopped, when it lapses, and who decided
// and why. See docs/approvals-and-risk.md. Approve sends back the payload hash shown here.
import { useId, useState, type ReactNode } from 'react';
import { CheckCircle2, Clock, Fingerprint, Globe, ShieldCheck, UserX, Users, XCircle } from 'lucide-react';
import type { Approval } from '@anum/contracts';
import { REASON_MAX_CHARS, approvalProgress, argumentRows, canApprove, decisionSummary, effectiveStatus, expiryLabel, hasApproved, joinNames, normalizeReason, shortHash } from './lib/approvals';

/**
 * Resolves with the API's refusal when the decision is not allowed (403, for example the
 * two-person rule refusing to let you approve your own high-risk task), otherwise with null.
 */
type Decide = (approval: Approval, decision: 'approve' | 'reject', reason?: string) => Promise<string | null>;

const formatDate = (value: string) =>
  new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));

export function ApprovalsView({ items, busy, decide, metric, empty, userId }: {
  items: Approval[];
  busy: boolean;
  decide: Decide;
  metric(icon: ReactNode, label: string, value: string, detail: string): ReactNode;
  empty: ReactNode;
  /** The signed-in user, so a chain they already approved offers no second approval. */
  userId?: string | null;
}) {
  const now = new Date();
  const status = (a: Approval) => effectiveStatus(a, now);
  const pending = items.filter((a) => status(a) === 'pending');
  const history = items
    .filter((a) => status(a) !== 'pending')
    .sort((a, b) => (b.decidedAt ?? b.createdAt).localeCompare(a.decidedAt ?? a.createdAt));
  return (
    <>
      <section className="metrics three">
        {metric(<ShieldCheck />, 'Pending', String(pending.length), 'Needs review')}
        {metric(<CheckCircle2 />, 'Approved', String(items.filter((a) => status(a) === 'approved').length), 'Workspace total')}
        {metric(<XCircle />, 'Rejected', String(items.filter((a) => status(a) === 'rejected').length), 'Workspace total')}
      </section>
      <section className="surface approvalTable">
        {pending.map((a) => <PendingApproval key={a.id} approval={a} busy={busy} decide={decide} now={now} userId={userId} />)}
        {!pending.length && empty}
      </section>
      {history.length > 0 && (
        <section className="surface approvalTable approvalHistory" aria-label="Approval history">
          <p className="eyebrow">History</p>
          <h2>Decisions</h2>
          {history.map((a) => (
            <details className="approvalHistoryRow" key={a.id}>
              <summary>
                <span className={`statusBadge ${status(a) === 'approved' ? 'success' : 'dangerTone'}`}>{status(a)}</span>
                <strong>{a.action}</strong>
                <small>{decisionSummary(a, formatDate, now)}</small>
              </summary>
              {a.decisionReason && <p className="approvalReason"><span>Reason</span> {a.decisionReason}</p>}
              <ProgressLine approval={a} />
              <TargetLine approval={a} />
              <ArgumentList approval={a} />
              <HashLine approval={a} />
            </details>
          ))}
        </section>
      )}
    </>
  );
}

function PendingApproval({ approval, busy, decide, now, userId }: { approval: Approval; busy: boolean; decide: Decide; now: Date; userId?: string | null }) {
  const alreadyApproved = hasApproved(approval, userId);
  const approvable = canApprove(approval, now) && !alreadyApproved;
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
    <article className="approvalDetail" aria-label={`Approval for ${approval.action}`}>
      <div className="approvalIcon"><ShieldCheck /></div>
      <div className="approvalBody">
        <h3>The agent wants to call <code>{approval.action}</code></h3>
        <p>{approval.reason}</p>
        <ProgressLine approval={approval} />
        {progress && alreadyApproved && (
          <p className="approvalProgressNote">You approved this. It needs {progress.required - progress.collected} more approval{progress.required - progress.collected === 1 ? '' : 's'} from other people before it runs.</p>
        )}
        <TargetLine approval={approval} />
        <p className="approvalSubhead">Exactly what it will send</p>
        <ArgumentList approval={approval} />
        <HashLine approval={approval} />
        <small className="approvalMeta">
          {approval.riskLevel} risk · task {approval.taskId} · requested {formatDate(approval.createdAt)}
          {expiry && <> · <Clock aria-hidden size={12} /> {expiry}</>}
        </small>
        {!approval.payloadHash && <p className="approvalWarning">This approval is not bound to a payload hash and cannot be approved. Run the task again.</p>}
        <label className="approvalReasonField" htmlFor={reasonId}>
          <span>Reason (optional)</span>
          <textarea
            id={reasonId}
            value={reason}
            maxLength={REASON_MAX_CHARS}
            rows={2}
            placeholder="Why you approve or reject this; kept in the audit trail"
            onChange={(event) => setReason(event.target.value)}
          />
          <small>{reason.length}/{REASON_MAX_CHARS}</small>
        </label>
        {refusal && (
          <div className="approvalRefusal" role="alert">
            <UserX aria-hidden />
            <div><strong>Decision refused</strong><p>{refusal}</p></div>
          </div>
        )}
      </div>
      <div className="approvalActions">
        <button disabled={busy || !approvable} onClick={() => act('approve')}><CheckCircle2 />Approve</button>
        <button className="danger" disabled={busy} onClick={() => act('reject')}><XCircle />Reject</button>
      </div>
    </article>
  );
}

function ArgumentList({ approval }: { approval: Approval }) {
  const rows = argumentRows(approval.arguments);
  if (!rows.length) return <p className="muted">No arguments.</p>;
  return (
    <dl className="approvalArgs">
      {rows.map((row) => (
        <div className="approvalArg" key={row.key}>
          <dt>{row.key}</dt>
          <dd className={row.redacted ? 'redacted' : undefined}>
            {row.multiline ? <pre>{row.value}</pre> : <code>{row.value}</code>}
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
    <div className="approvalProgress" aria-label={`Approval progress: ${progress.label}`}>
      <Users aria-hidden size={14} />
      <strong>{progress.label}</strong>
      <meter min={0} max={progress.required} value={progress.collected} aria-hidden />
      <small>{progress.approvers.length ? `Approved by ${joinNames(progress.approvers)}` : 'No approvals yet'}</small>
    </div>
  );
}

function TargetLine({ approval }: { approval: Approval }) {
  if (!approval.target) return null;
  return (
    <small className="approvalTarget">
      <Globe aria-hidden size={12} /> Sends to <code>{approval.target}</code>
    </small>
  );
}

function HashLine({ approval }: { approval: Approval }) {
  return (
    <small className="approvalHash" title={approval.payloadHash ?? undefined}>
      <Fingerprint aria-hidden size={12} /> Payload hash <code>{shortHash(approval.payloadHash)}</code>
    </small>
  );
}
