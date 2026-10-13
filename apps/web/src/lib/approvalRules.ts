// Organization approval rules with AED bands and approver steps (docs/approvals-and-risk.md#approval-routing),
// and the plain words the Approvals cards use to say who may approve each item and why.
// Pure functions, so `pnpm --filter @anum/web test:unit` covers them without a DOM.
import type { Approval, ApprovalRouteReason, ApprovalRouting } from '@anum/contracts';
import { getLocale, intlLocale, joinList, t, type MessageKey } from './i18n.ts';

export type Disputed = 'any' | 'yes' | 'no';

export interface RuleConditions {
  amount_field?: string | null;
  currency?: 'AED';
  amount_above?: number | null;
  amount_at_most?: number | null;
  order_value_field?: string | null;
  percent_of_order_at_most?: number | null;
  disputed?: Disputed;
}

export interface ApprovalRule {
  id: string;
  name: string;
  action_pattern: string;
  minimum_approvers: number;
  required_roles: string[];
  enabled: boolean;
  created_at: string;
  conditions?: RuleConditions;
  approver_steps?: { approvers: string[] }[];
  updated_at?: string | null;
  updated_by?: string | null;
}

export interface ApprovalRuleBody {
  name: string;
  action_pattern: string;
  minimum_approvers: number;
  required_roles: string[];
  enabled: boolean;
  conditions: RuleConditions;
  approver_steps: { approvers: string[] }[];
}

export interface ApproverDraft { owner: boolean; orgAdmin: boolean; members: boolean; people: string }

export interface RuleDraft {
  name: string;
  actionPattern: string;
  enabled: boolean;
  amountAbove: string;
  amountAtMost: string;
  percentAtMost: string;
  disputed: Disputed;
  amountField: string;
  orderValueField: string;
  first: ApproverDraft;
  twoSteps: boolean;
  second: ApproverDraft;
}

export type Result<T> = { ok: true; value: T } | { ok: false; error: string };

export const MAX_AED = 100_000_000;
const USER_ID = /^[A-Za-z0-9_.@:+|-]{1,160}$/;
const FIELD = /^[A-Za-z_][A-Za-z0-9_]{0,63}(\.[A-Za-z_][A-Za-z0-9_]{0,63})?$/;

const ownersOnly = (): ApproverDraft => ({ owner: true, orgAdmin: false, members: false, people: '' });

export function emptyRuleDraft(): RuleDraft {
  return {
    name: '', actionPattern: '', enabled: true, amountAbove: '', amountAtMost: '', percentAtMost: '', disputed: 'any',
    amountField: '', orderValueField: '', first: ownersOnly(), twoSteps: false, second: ownersOnly(),
  };
}

function approverDraft(tokens: string[] | undefined): ApproverDraft {
  const list = tokens ?? [];
  return {
    owner: list.includes('owner') || list.includes('role:owner'),
    orgAdmin: list.includes('org_admin'),
    members: list.includes('role:member'),
    people: list.filter((token) => token.startsWith('user:')).map((token) => token.slice(5)).join(', '),
  };
}

const text = (value: number | null | undefined) => (value === null || value === undefined ? '' : String(value));

/** The editor's draft for a stored rule. A rule without steps is shown as "owners". */
export function draftFromRule(rule: ApprovalRule): RuleDraft {
  const c = rule.conditions ?? {};
  const steps = rule.approver_steps ?? [];
  return {
    name: rule.name, actionPattern: rule.action_pattern, enabled: rule.enabled,
    amountAbove: text(c.amount_above), amountAtMost: text(c.amount_at_most), percentAtMost: text(c.percent_of_order_at_most),
    disputed: c.disputed ?? 'any', amountField: c.amount_field ?? '', orderValueField: c.order_value_field ?? '',
    first: steps[0] ? approverDraft(steps[0].approvers) : ownersOnly(),
    twoSteps: steps.length > 1,
    second: steps[1] ? approverDraft(steps[1].approvers) : ownersOnly(),
  };
}

/** An AED amount typed by a person: digits with optional thousands commas and up to 2 decimals. */
export function parseAed(value: string): number | null | 'invalid' {
  const cleaned = value.replace(/aed/i, '').replace(/[\s,٬]/g, '').trim();
  if (!cleaned) return null;
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return 'invalid';
  const number = Number(cleaned);
  return number <= MAX_AED ? number : 'invalid';
}

function tokens(draft: ApproverDraft): Result<string[]> {
  const people = [...new Set(draft.people.split(/[,\n]/).map((x) => x.trim()).filter(Boolean))];
  const bad = people.find((id) => !USER_ID.test(id));
  if (bad) return { ok: false, error: t('rules.err.person', { id: bad }) };
  const list = [
    ...(draft.owner ? ['owner'] : []),
    ...(draft.orgAdmin ? ['org_admin'] : []),
    ...(draft.members ? ['role:member'] : []),
    ...people.map((id) => `user:${id}`),
  ];
  if (!list.length) return { ok: false, error: t('rules.err.noApprovers') };
  if (list.length > 20) return { ok: false, error: t('rules.err.tooManyApprovers') };
  return { ok: true, value: list };
}

/** The `POST` / `PUT /organization/approval-rules` body, or the first problem in plain words. */
export function ruleBody(draft: RuleDraft): Result<ApprovalRuleBody> {
  const name = draft.name.trim();
  const pattern = draft.actionPattern.trim();
  if (!name) return { ok: false, error: t('rules.err.name') };
  if (name.length > 160) return { ok: false, error: t('rules.err.nameLength') };
  if (!pattern) return { ok: false, error: t('rules.err.pattern') };
  if (pattern.length > 160) return { ok: false, error: t('rules.err.patternLength') };
  const above = parseAed(draft.amountAbove);
  const atMost = parseAed(draft.amountAtMost);
  if (above === 'invalid' || atMost === 'invalid') return { ok: false, error: t('rules.err.amount') };
  if (above !== null && atMost !== null && atMost <= above) return { ok: false, error: t('rules.err.band') };
  let percent: number | null = null;
  if (draft.percentAtMost.trim()) {
    percent = Number(draft.percentAtMost.replace('%', '').trim());
    if (!Number.isFinite(percent) || percent <= 0 || percent > 100) return { ok: false, error: t('rules.err.percent') };
  }
  for (const field of [draft.amountField, draft.orderValueField]) {
    if (field.trim() && !FIELD.test(field.trim())) return { ok: false, error: t('rules.err.field', { field: field.trim() }) };
  }
  const first = tokens(draft.first);
  if (!first.ok) return first;
  const steps = [{ approvers: first.value }];
  if (draft.twoSteps) {
    const second = tokens(draft.second);
    if (!second.ok) return second;
    steps.push({ approvers: second.value });
  }
  const conditions: RuleConditions = { currency: 'AED', disputed: draft.disputed };
  if (above !== null) conditions.amount_above = above;
  if (atMost !== null) conditions.amount_at_most = atMost;
  if (percent !== null) conditions.percent_of_order_at_most = percent;
  if (draft.amountField.trim()) conditions.amount_field = draft.amountField.trim();
  if (draft.orderValueField.trim()) conditions.order_value_field = draft.orderValueField.trim();
  return {
    ok: true,
    value: { name, action_pattern: pattern, minimum_approvers: 1, required_roles: ['owner'], enabled: draft.enabled, conditions, approver_steps: steps },
  };
}

/** "AED 5,000" (Western digits in both languages). */
export function formatAed(value: number | string): string {
  const number = typeof value === 'string' ? Number(value) : value;
  const amount = Number.isFinite(number)
    ? new Intl.NumberFormat(intlLocale(getLocale()), { maximumFractionDigits: 2 }).format(number)
    : String(value);
  return t('rules.aed', { amount });
}

/** One approver token in words: "Owners", "Members", "Organization administrators" or the person's id. */
export function approverLabel(token: string): string {
  if (token === 'owner' || token === 'role:owner') return t('routing.who.owners');
  if (token === 'org_admin') return t('routing.who.orgAdmins');
  if (token === 'role:member') return t('routing.who.members');
  return token.startsWith('user:') ? token.slice(5) : token;
}

/** Anyone of a list: "Owners or care_lead". */
export function approverSetLabel(set: readonly string[]): string {
  const labels = [...new Set(set.map(approverLabel))];
  if (labels.length <= 1) return labels[0] ?? '';
  return t('routing.or', { head: labels.slice(0, -1).join(t('common.listSeparator')), last: labels[labels.length - 1] });
}

/** Who may decide one step: "Owners only", "Owners or care_lead", or both lists when two rules apply. */
export function stepLabel(step: readonly (readonly string[])[]): string {
  if (step.length === 1 && step[0].length === 1) return t('routing.only', { who: approverSetLabel(step[0]) }, { isolate: false });
  return joinList(step.map((set) => approverSetLabel(set)));
}

/** The band a rule applies to, in words, or null when it applies to every amount. */
export function ruleBand(conditions: RuleConditions | undefined): string | null {
  const parts: string[] = [];
  const c = conditions ?? {};
  if (c.amount_above !== null && c.amount_above !== undefined && c.amount_at_most !== null && c.amount_at_most !== undefined) {
    parts.push(t('rules.band.between', { above: formatAed(c.amount_above), atMost: formatAed(c.amount_at_most) }));
  } else if (c.amount_above !== null && c.amount_above !== undefined) {
    parts.push(t('rules.band.above', { amount: formatAed(c.amount_above) }));
  } else if (c.amount_at_most !== null && c.amount_at_most !== undefined) {
    parts.push(t('rules.band.atMost', { amount: formatAed(c.amount_at_most) }));
  }
  if (c.percent_of_order_at_most) parts.push(t('rules.band.percent', { percent: c.percent_of_order_at_most }));
  if (c.disputed === 'yes') parts.push(t('rules.band.disputed'));
  if (c.disputed === 'no') parts.push(t('rules.band.notDisputed'));
  return parts.length ? parts.join(' · ') : null;
}

/** Who approves a stored rule, step by step. A rule without steps keeps the original behaviour. */
export function ruleSteps(rule: ApprovalRule): string[] {
  const steps = rule.approver_steps ?? [];
  if (!steps.length) {
    return [t('rules.legacy', { count: rule.minimum_approvers, roles: rule.required_roles.join(', ') || 'owner' })];
  }
  return steps.map((step, index) => steps.length > 1
    ? t('rules.step', { step: index + 1, who: approverSetLabel(step.approvers) }, { isolate: false })
    : approverSetLabel(step.approvers));
}

// ---------------------------------------------------------------- the Approvals cards

const money = (value: string | null | undefined) => (value ? formatAed(value) : '');

/** One reason in words, or null for reasons that only list a rule. */
export function reasonText(reason: ApprovalRouteReason): string | null {
  const rule = reason.rule ?? '';
  const related = reason.related > 0 ? t('routing.related', { count: reason.related }) : '';
  switch (reason.code) {
    case 'amount_above': return t(reason.applies ? 'routing.reason.above' : 'routing.reason.notAbove', { rule, total: money(reason.total), threshold: money(reason.threshold) }) + related;
    case 'amount_at_most': return t(reason.applies ? 'routing.reason.atMost' : 'routing.reason.overBand', { rule, total: money(reason.total), threshold: money(reason.threshold) }) + related;
    case 'amount_unknown': return t('routing.reason.unknown', { rule });
    case 'percent_at_most': return t(reason.applies ? 'routing.reason.percent' : 'routing.reason.overPercent', { rule, percent: reason.percent ?? '', threshold: reason.threshold ?? '' });
    case 'percent_unknown': return t('routing.reason.percentUnknown', { rule });
    case 'disputed': return t('routing.reason.disputed', { rule });
    case 'no_band': return t('routing.reason.noBand');
    default: return null;
  }
}

export interface RoutingView {
  /** Why it went to these approvers: "Refunds above AED 250: AED 300 is more than AED 250". */
  why: string[];
  /** Who may decide the step that waits: "Owners only". */
  who: string;
  /** "Step 2 of 2" when there are two steps. */
  step: string | null;
  /** Each step with who may approve it and who did. */
  steps: { label: string; who: string; approvedBy: string | null; current: boolean }[];
  /** Read-only explanation for someone who cannot approve, or null. */
  explanation: string | null;
}

/** What a routed approval card shows; null when no amount rule routes it (owners decide as before). */
export function routingView(approval: Approval): RoutingView | null {
  const routing: ApprovalRouting | null | undefined = approval.routing;
  if (!routing || !routing.routed) return null;
  const applied = routing.reasons.filter((reason) => reason.applies || routing.fallback);
  const why = applied.map(reasonText).filter((line): line is string => Boolean(line));
  const index = Math.min(routing.currentStep, Math.max(0, routing.steps.length - 1));
  const who = routing.steps[index] ? stepLabel(routing.steps[index].approvers) : t('routing.only', { who: t('routing.who.owners') }, { isolate: false });
  const count = routing.steps.length;
  return {
    why: why.length ? why : routing.rules.map((rule) => t('routing.reason.rule', { rule })),
    who,
    step: count > 1 ? t('routing.stepOf', { step: index + 1, count }) : null,
    steps: routing.steps.map((step, i) => ({
      label: t('routing.stepOf', { step: i + 1, count }),
      who: stepLabel(step.approvers),
      approvedBy: step.approvedBy ?? null,
      current: i === index && approval.status === 'pending',
    })),
    explanation: refusalText(routing, who),
  };
}

const REFUSALS: Record<string, MessageKey> = {
  not_listed: 'routing.refusal.notListed',
  own_request: 'routing.refusal.own',
  already_approved: 'routing.refusal.already',
  viewer: 'routing.refusal.viewer',
};

/** Why the signed-in person cannot approve, or null when they can (or the item is decided). */
export function refusalText(routing: ApprovalRouting | null | undefined, who: string): string | null {
  if (!routing || routing.canApprove || !routing.refusal || routing.refusal === 'not_pending') return null;
  const key = REFUSALS[routing.refusal];
  return key ? t(key, { who }, { isolate: false }) : null;
}

/** Whether Approve may be offered for this person (the API's answer; unknown means the API decides). */
export const mayApprove = (approval: Approval): boolean => approval.routing ? approval.routing.canApprove : true;
/** Whether Reject may be offered for this person. */
export const mayReject = (approval: Approval): boolean => approval.routing ? approval.routing.canReject : true;

/** The opted-out recipients of a waiting send, in words. */
export function suppressedText(approval: Approval): string | null {
  const list = approval.suppressed ?? [];
  if (!list.length) return null;
  return t('routing.suppressed', { recipients: joinList(list.map((item) => item.recipient)), count: list.length });
}
