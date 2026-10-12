// Workspace approval policy: wire mapping and the plain-words explanation of each switch.
// docs/approvals-and-risk.md (Workspace Approval Policy). Pure functions, so
// `pnpm --filter @anum/web test:unit` can exercise them without a DOM.
import type { WorkspaceApprovalPolicy, WorkspaceMember } from '@anum/contracts';
import { t, type MessageKey } from './i18n.ts';

/** `GET`/`PUT /api/v1/approval-policy` answer. */
export interface ApiApprovalPolicy {
  two_person_rule: boolean;
  medium_risk_requires_approval: boolean;
  updated_by?: string | null;
  updated_at?: string | null;
}

/** `GET /api/v1/workspace-memberships/current` answer (only the fields the clients read). */
export interface ApiCurrentMembership { tenant_id: string; workspace_id: string; user_id: string; role: string; active: boolean }

export type PolicyKey = 'twoPersonRule' | 'mediumRiskRequiresApproval';
export type PolicyDraft = Record<PolicyKey, boolean>;

export function mapApprovalPolicy(value: ApiApprovalPolicy): WorkspaceApprovalPolicy {
  return {
    twoPersonRule: Boolean(value.two_person_rule),
    mediumRiskRequiresApproval: Boolean(value.medium_risk_requires_approval),
    updatedBy: value.updated_by ?? null,
    updatedAt: value.updated_at ?? null,
  };
}

/** The owner-only `PUT` body: both switches, always. */
export function approvalPolicyBody(draft: PolicyDraft): { two_person_rule: boolean; medium_risk_requires_approval: boolean } {
  return { two_person_rule: draft.twoPersonRule, medium_risk_requires_approval: draft.mediumRiskRequiresApproval };
}

export const policyDraft = (policy: WorkspaceApprovalPolicy): PolicyDraft => ({
  twoPersonRule: policy.twoPersonRule,
  mediumRiskRequiresApproval: policy.mediumRiskRequiresApproval,
});

export const policyChanged = (policy: WorkspaceApprovalPolicy, draft: PolicyDraft): boolean =>
  policy.twoPersonRule !== draft.twoPersonRule || policy.mediumRiskRequiresApproval !== draft.mediumRiskRequiresApproval;

/** A switch whose words are read in the current page language (policy.* messages). */
function policySwitch(key: PolicyKey, prefix: 'twoPerson' | 'medium'): { key: PolicyKey; title: string; on: string; off: string } {
  const text = (part: 'title' | 'on' | 'off') => t(`policy.${prefix}.${part}` as MessageKey);
  return { key, get title() { return text('title'); }, get on() { return text('on'); }, get off() { return text('off'); } };
}

/** What each switch does, in the words the screens show. Same text in the Flutter client. */
export const POLICY_SWITCHES: readonly { key: PolicyKey; title: string; on: string; off: string }[] = [
  policySwitch('twoPersonRule', 'twoPerson'),
  policySwitch('mediumRiskRequiresApproval', 'medium'),
];

/** "Last changed ... by ..." or the default, for the policy's footer. */
export function policyUpdatedLabel(policy: WorkspaceApprovalPolicy, format: (iso: string) => string): string {
  if (!policy.updatedAt) return t('policy.neverChanged');
  return policy.updatedBy
    ? t('policy.lastChangedBy', { when: format(policy.updatedAt), user: policy.updatedBy })
    : t('policy.lastChanged', { when: format(policy.updatedAt) });
}

/**
 * Warning when the two-person rule would leave nobody able to approve: only owners decide
 * approvals, so the rule needs at least two active owners. Null when fine or unknown.
 */
export function twoPersonWarning(draft: PolicyDraft, members: WorkspaceMember[] | null): string | null {
  if (!draft.twoPersonRule || members === null) return null;
  const owners = members.filter((m) => m.active && m.role === 'owner').length;
  if (owners >= 2) return null;
  return owners === 1 ? t('policy.warnOneOwner') : t('policy.warnNoOwner');
}
