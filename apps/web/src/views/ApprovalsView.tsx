import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Approval, ApprovalStatus, RiskLevel, TenantContext } from '@anum/contracts';
import {
  CheckCircle2,
  Clock,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  XCircle,
} from 'lucide-react';
import { ApiError, decideApproval, listApprovals } from '../lib/api';

type Decision = 'approve' | 'reject';

const RISK_LABEL: Record<RiskLevel, string> = {
  low: 'Low risk',
  medium: 'Medium risk',
  high: 'High risk',
  blocked: 'Blocked',
};

const RISK_CLASS: Record<RiskLevel, string> = {
  low: 'riskPill--low',
  medium: 'riskPill--medium',
  high: 'riskPill--high',
  blocked: 'riskPill--blocked',
};

const STATUS_META: Record<ApprovalStatus, { label: string; className: string; icon: typeof Clock }> = {
  pending: { label: 'Pending review', className: 'pill--warning', icon: Clock },
  approved: { label: 'Approved', className: 'pill--success', icon: CheckCircle2 },
  rejected: { label: 'Rejected', className: 'pill--danger', icon: XCircle },
  expired: { label: 'Expired', className: 'badge', icon: ShieldAlert },
};

function formatTimestamp(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return parsed.toLocaleString();
}

function RiskPill({ level }: { level: RiskLevel }) {
  return (
    <span className={RISK_CLASS[level]} title={`Risk level: ${RISK_LABEL[level]}`}>
      {RISK_LABEL[level]}
    </span>
  );
}

function StatusPill({ status }: { status: ApprovalStatus }) {
  const meta = STATUS_META[status];
  const Icon = meta.icon;
  return (
    <span className={meta.className} style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-1)' }}>
      <Icon size={14} aria-hidden="true" />
      {meta.label}
    </span>
  );
}

interface ApprovalRowProps {
  approval: Approval;
  showActions: boolean;
  deciding: boolean;
  notice?: string;
  onApprove: () => void;
  onReject: () => void;
}

function ApprovalRow({ approval, showActions, deciding, notice, onApprove, onReject }: ApprovalRowProps) {
  return (
    <div className="listRow" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
        <div>
          <strong>{approval.action}</strong>
          <p>{approval.reason}</p>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <RiskPill level={approval.riskLevel} />
          <StatusPill status={approval.status} />
        </div>
      </div>
      <p style={{ color: 'var(--color-text-subtle)', fontSize: 'var(--text-xs)', marginTop: 'var(--space-2)' }}>
        Task {approval.taskId} &middot; requested {formatTimestamp(approval.createdAt)}
      </p>
      {notice && (
        <p className="errorNotice" role="alert" style={{ marginTop: 'var(--space-2)' }}>
          {notice}
        </p>
      )}
      {showActions && (
        <div className="actions" style={{ marginTop: 'var(--space-3)' }}>
          <button type="button" onClick={onApprove} disabled={deciding}>
            <CheckCircle2 size={16} aria-hidden="true" />
            {deciding ? 'Approving…' : 'Approve'}
          </button>
          <button type="button" className="danger" onClick={onReject} disabled={deciding}>
            <XCircle size={16} aria-hidden="true" />
            {deciding ? 'Rejecting…' : 'Reject'}
          </button>
        </div>
      )}
    </div>
  );
}

export default function ApprovalsView({ tenantContext: _tenantContext }: { tenantContext: TenantContext }) {
  const [approvals, setApprovals] = useState<Approval[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const [rowNotices, setRowNotices] = useState<Record<string, string>>({});

  const fetchApprovals = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await listApprovals();
      setApprovals(data);
    } catch (err) {
      setLoadError(err instanceof Error ? err : new Error('Failed to load approvals.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchApprovals();
  }, [fetchApprovals]);

  const { pending, decided } = useMemo(() => {
    const list = approvals ?? [];
    const byRecency = (a: Approval, b: Approval) =>
      new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
    return {
      pending: list.filter((a) => a.status === 'pending').sort(byRecency),
      decided: list.filter((a) => a.status !== 'pending').sort(byRecency),
    };
  }, [approvals]);

  const handleDecision = useCallback(
    async (approval: Approval, decision: Decision) => {
      setDecidingId(approval.id);
      setRowNotices((prev) => {
        if (!(approval.id in prev)) return prev;
        const next = { ...prev };
        delete next[approval.id];
        return next;
      });

      try {
        const result = await decideApproval(approval.id, decision);
        setApprovals((prev) => (prev ? prev.map((a) => (a.id === result.approval.id ? result.approval : a)) : prev));
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          setRowNotices((prev) => ({
            ...prev,
            [approval.id]: 'Someone else already decided this request. Refreshed with the latest status below.',
          }));
          await fetchApprovals();
        } else if (err instanceof ApiError) {
          setRowNotices((prev) => ({ ...prev, [approval.id]: err.message }));
        } else {
          setRowNotices((prev) => ({ ...prev, [approval.id]: 'Failed to record decision. Try again.' }));
        }
      } finally {
        setDecidingId(null);
      }
    },
    [fetchApprovals],
  );

  const hasApprovals = approvals !== null && approvals.length > 0;

  return (
    <div>
      <div className="viewHeader">
        <div>
          <p className="eyebrow">Risk control</p>
          <h2>Approval queue</h2>
        </div>
        <button type="button" className="secondary" onClick={fetchApprovals} disabled={loading}>
          <RefreshCw size={16} aria-hidden="true" />
          Refresh
        </button>
      </div>

      {loading && approvals === null && (
        <div className="card">
          <div className="list">
            <div className="skeleton" style={{ height: 76 }} />
            <div className="skeleton" style={{ height: 76 }} />
            <div className="skeleton" style={{ height: 76 }} />
          </div>
        </div>
      )}

      {!loading && loadError && (
        <div className="errorNotice" role="alert">
          <p>{loadError.message}</p>
          <div className="actions" style={{ marginTop: 'var(--space-3)' }}>
            <button type="button" onClick={fetchApprovals}>
              Retry
            </button>
          </div>
        </div>
      )}

      {!loading && !loadError && approvals !== null && !hasApprovals && (
        <div className="emptyState">
          <ShieldCheck size={28} aria-hidden="true" />
          <p>Nothing is waiting on your review.</p>
        </div>
      )}

      {!loading && !loadError && hasApprovals && (
        <>
          <section className="card">
            <div className="panelHeader">
              <h3>Needs your decision</h3>
              <span className="badge">{pending.length}</span>
            </div>
            {pending.length === 0 ? (
              <div className="emptyState">
                <ShieldCheck size={24} aria-hidden="true" />
                <p>No pending approvals right now.</p>
              </div>
            ) : (
              <div className="list">
                {pending.map((approval) => (
                  <ApprovalRow
                    key={approval.id}
                    approval={approval}
                    showActions
                    deciding={decidingId === approval.id}
                    notice={rowNotices[approval.id]}
                    onApprove={() => handleDecision(approval, 'approve')}
                    onReject={() => handleDecision(approval, 'reject')}
                  />
                ))}
              </div>
            )}
          </section>

          {decided.length > 0 && (
            <section className="panel">
              <div className="panelHeader">
                <h3>Decision history</h3>
                <span className="badge">{decided.length}</span>
              </div>
              <div className="list">
                {decided.map((approval) => (
                  <ApprovalRow
                    key={approval.id}
                    approval={approval}
                    showActions={false}
                    deciding={false}
                    notice={rowNotices[approval.id]}
                    onApprove={() => {}}
                    onReject={() => {}}
                  />
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
