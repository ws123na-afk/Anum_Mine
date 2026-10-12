// Approvals: the exact tool call, where it goes, why it stopped, when it lapses, and who decided
// and why. See docs/approvals-and-risk.md. Approve sends back the payload hash shown here.
import { useId, useState, type ReactNode } from 'react';
import { CheckCircle2, Clock, Fingerprint, Globe, ShieldCheck, UserX, Users, XCircle } from 'lucide-react';
import type { Approval } from '@anum/contracts';
import { REASON_MAX_CHARS, approvalProgress, argumentRows, canApprove, decisionSummary, effectiveStatus, expiryLabel, hasApproved, joinNames, normalizeReason, shortHash } from './lib/approvals';
import { formatDateTime, t } from './lib/i18n';
import { riskLabel, statusLabel } from './lib/labels';
import { rich } from './rich';

/**
 * Resolves with the API's refusal when the decision is not allowed (403, for example the
 * two-person rule refusing to let you approve your own high-risk task), otherwise with null.
 */
type Decide = (approval: Approval, decision: 'approve' | 'reject', reason?: string) => Promise<string | null>;

const formatDate = (value: string) => formatDateTime(value);

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
        {metric(<ShieldCheck />, t('approvals.metric.pending'), String(pending.length), t('approvals.metric.pendingDetail'))}
        {metric(<CheckCircle2 />, t('approvals.metric.approved'), String(items.filter((a) => status(a) === 'approved').length), t('approvals.metric.total'))}
        {metric(<XCircle />, t('approvals.metric.rejected'), String(items.filter((a) => status(a) === 'rejected').length), t('approvals.metric.total'))}
      </section>
      <section className="surface approvalTable" aria-labelledby="approvals-pending-heading">
        <h2 className="visuallyHidden" id="approvals-pending-heading">{t('approvals.pendingHeading')}</h2>
        {pending.map((a) => <PendingApproval key={a.id} approval={a} busy={busy} decide={decide} now={now} userId={userId} />)}
        {!pending.length && empty}
      </section>
      {history.length > 0 && (
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
    <article className="approvalDetail" aria-label={t('approvals.cardLabel', { action: approval.action })}>
      <div className="approvalIcon" aria-hidden="true"><ShieldCheck /></div>
      <div className="approvalBody">
        <h3>{rich('approvals.wantsToCall', { action: <code dir="ltr">{approval.action}</code> })}</h3>
        <p dir="auto">{approval.reason}</p>
        <ProgressLine approval={approval} />
        {progress && alreadyApproved && (
          <p className="approvalProgressNote">{t('approvals.youApproved', { count: progress.required - progress.collected })}</p>
        )}
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
        <button className="danger" disabled={busy} onClick={() => act('reject')}><XCircle aria-hidden="true" />{t('approvals.reject')}</button>
      </div>
    </article>
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
