// Approvals: the exact tool call, why it stopped, when it lapses, and who decided.
// See docs/approvals-and-risk.md. Approve sends back the payload hash shown here.
import type { ReactNode } from 'react';
import { CheckCircle2, Clock, Fingerprint, ShieldCheck, XCircle } from 'lucide-react';
import type { Approval } from '@anum/contracts';
import { argumentRows, canApprove, decisionSummary, effectiveStatus, expiryLabel, shortHash } from './lib/approvals';

type Decide = (approval: Approval, decision: 'approve' | 'reject') => void;

const formatDate = (value: string) =>
  new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));

export function ApprovalsView({ items, busy, decide, metric, empty }: {
  items: Approval[];
  busy: boolean;
  decide: Decide;
  metric(icon: ReactNode, label: string, value: string, detail: string): ReactNode;
  empty: ReactNode;
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
        {pending.map((a) => <PendingApproval key={a.id} approval={a} busy={busy} decide={decide} now={now} />)}
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
              <ArgumentList approval={a} />
              <HashLine approval={a} />
            </details>
          ))}
        </section>
      )}
    </>
  );
}

function PendingApproval({ approval, busy, decide, now }: { approval: Approval; busy: boolean; decide: Decide; now: Date }) {
  const approvable = canApprove(approval, now);
  const expiry = expiryLabel(approval, now);
  return (
    <article className="approvalDetail" aria-label={`Approval for ${approval.action}`}>
      <div className="approvalIcon"><ShieldCheck /></div>
      <div className="approvalBody">
        <h3>The agent wants to call <code>{approval.action}</code></h3>
        <p>{approval.reason}</p>
        <p className="approvalSubhead">Exactly what it will send</p>
        <ArgumentList approval={approval} />
        <HashLine approval={approval} />
        <small className="approvalMeta">
          {approval.riskLevel} risk · task {approval.taskId} · requested {formatDate(approval.createdAt)}
          {expiry && <> · <Clock aria-hidden size={12} /> {expiry}</>}
        </small>
        {!approval.payloadHash && <p className="approvalWarning">This approval is not bound to a payload hash and cannot be approved. Run the task again.</p>}
      </div>
      <div className="approvalActions">
        <button disabled={busy || !approvable} onClick={() => decide(approval, 'approve')}><CheckCircle2 />Approve</button>
        <button className="danger" disabled={busy} onClick={() => decide(approval, 'reject')}><XCircle />Reject</button>
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

function HashLine({ approval }: { approval: Approval }) {
  return (
    <small className="approvalHash" title={approval.payloadHash ?? undefined}>
      <Fingerprint aria-hidden size={12} /> Payload hash <code>{shortHash(approval.payloadHash)}</code>
    </small>
  );
}
