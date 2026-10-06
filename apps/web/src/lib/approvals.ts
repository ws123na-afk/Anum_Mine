// Approval display and decision helpers (docs/approvals-and-risk.md).
// Pure functions, so `pnpm --filter @anum/web test:unit` can exercise them without a DOM.
import type { Approval, ApprovalStatus } from '@anum/contracts';

export const REDACTED = '[REDACTED]';

export interface ArgumentRow {
  /** Dotted path of the argument, e.g. `headers.accept` or `items[0]`. */
  key: string;
  /** The value as text; long text stays whole so the approver sees everything. */
  value: string;
  redacted: boolean;
  multiline: boolean;
}

/** Flatten the tool arguments into readable rows, one per leaf value, in key order. */
export function argumentRows(args: Record<string, unknown> | undefined | null): ArgumentRow[] {
  const rows: ArgumentRow[] = [];
  const visit = (key: string, value: unknown): void => {
    if (Array.isArray(value)) {
      if (!value.length) rows.push(row(key, '[]'));
      value.forEach((item, index) => visit(`${key}[${index}]`, item));
      return;
    }
    if (value !== null && typeof value === 'object') {
      const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b));
      if (!entries.length) rows.push(row(key, '{}'));
      for (const [child, item] of entries) visit(key ? `${key}.${child}` : child, item);
      return;
    }
    rows.push(row(key, value === null || value === undefined ? 'null' : String(value)));
  };
  for (const [key, value] of Object.entries(args ?? {}).sort(([a], [b]) => a.localeCompare(b))) visit(key, value);
  return rows;
}

function row(key: string, value: string): ArgumentRow {
  return { key: key || '(value)', value, redacted: value === REDACTED, multiline: value.includes('\n') || value.length > 80 };
}

/** What a list shows: a pending approval past its expiry reads as expired. */
export function effectiveStatus(approval: Approval, now: Date = new Date()): ApprovalStatus {
  if (approval.status === 'pending' && approval.expiresAt && Date.parse(approval.expiresAt) <= now.getTime()) {
    return 'expired';
  }
  return approval.status;
}

/** Whether the Approve button may be offered: pending, unexpired and bound to a hash. */
export function canApprove(approval: Approval, now: Date = new Date()): boolean {
  return effectiveStatus(approval, now) === 'pending' && Boolean(approval.payloadHash);
}

/** Longest decision reason the API accepts. */
export const REASON_MAX_CHARS = 500;

/** A typed reason as sent: trimmed, blank means none. */
export function normalizeReason(reason: string | null | undefined): string | undefined {
  const trimmed = (reason ?? '').replace(/\r\n/g, '\n').trim();
  return trimmed ? trimmed.slice(0, REASON_MAX_CHARS) : undefined;
}

export interface DecisionBody {
  payload_hash?: string;
  reason?: string;
}

/** The decision body: the payload hash exactly as it was displayed, and the optional reason. */
export function decisionBody(approval: Approval, reason?: string | null): DecisionBody | undefined {
  const body: DecisionBody = {};
  if (approval.payloadHash) body.payload_hash = approval.payloadHash;
  const normalized = normalizeReason(reason);
  if (normalized) body.reason = normalized;
  return Object.keys(body).length ? body : undefined;
}

/** Short form of the hash for display; the full value is in the title attribute. */
export function shortHash(hash: string | null | undefined): string {
  return hash ? `${hash.slice(0, 12)}…${hash.slice(-6)}` : 'not bound';
}

/** "Expires in 3 h" / "Expired" for a pending approval, otherwise an empty string. */
export function expiryLabel(approval: Approval, now: Date = new Date()): string {
  if (approval.status !== 'pending' || !approval.expiresAt) return '';
  const remaining = Date.parse(approval.expiresAt) - now.getTime();
  if (remaining <= 0) return 'Expired';
  const minutes = Math.ceil(remaining / 60_000);
  if (minutes < 60) return `Expires in ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return hours < 48 ? `Expires in ${hours} h` : `Expires in ${Math.floor(hours / 24)} d`;
}

/** History line: who decided and when, or how it ended without a decision. */
export function decisionSummary(approval: Approval, format: (iso: string) => string, now: Date = new Date()): string {
  const status = effectiveStatus(approval, now);
  const when = approval.decidedAt ? ` ${format(approval.decidedAt)}` : '';
  if (status === 'approved' || status === 'rejected') {
    const verb = status === 'approved' ? 'Approved' : 'Rejected';
    const approvers = (approval.approvers ?? []).map((approver) => approver.userId);
    const who = status === 'approved' && approvers.length > 1 ? joinNames(approvers) : approval.decidedBy ?? 'unknown user';
    return `${verb} by ${who}${when ? ` ·${when}` : ''}`;
  }
  if (status === 'expired') {
    const at = approval.decidedAt ?? approval.expiresAt;
    return `Expired without a decision${at ? ` · ${format(at)}` : ''}`;
  }
  return '';
}

export interface ApprovalProgress {
  /** Distinct approvals recorded so far. */
  collected: number;
  /** Distinct approvals the organization rules require. */
  required: number;
  /** "1 of 3 approvals". */
  label: string;
  /** Who approved so far, oldest first. */
  approvers: string[];
}

/**
 * Progress of an approval chain (docs/approvals-and-risk.md, Approval chains), or null when the
 * approval needs a single decision.
 */
export function approvalProgress(approval: Approval): ApprovalProgress | null {
  const required = approval.requiredApprovals ?? 1;
  const approvers = (approval.approvers ?? []).map((approver) => approver.userId);
  if (required <= 1 && approvers.length <= 1) return null;
  const total = Math.max(required, approvers.length);
  return { collected: approvers.length, required: total, label: `${approvers.length} of ${total} approvals`, approvers };
}

/** Whether this user already approved (the API refuses a second approval by the same person). */
export function hasApproved(approval: Approval, userId: string | null | undefined): boolean {
  return Boolean(userId) && (approval.approvers ?? []).some((approver) => approver.userId === userId);
}

/** Names joined for a sentence: "a", "a and b", "a, b and c". */
export function joinNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? '';
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}
