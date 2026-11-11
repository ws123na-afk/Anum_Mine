// The Approvals settings page (docs/approvals-and-risk.md#approvals-settings): the routing section is read
// from the organization's approval rules and the workspace policy, in the order the API applies them.
// Pure, so `pnpm --filter @anum/web test:unit` covers it without a DOM.
import type { WorkspaceApprovalPolicy } from '@anum/contracts';
import { ruleBand, ruleSteps, type ApprovalRule } from './approvalRules.ts';
import { t } from './i18n.ts';

export type SettingsSection = 'policy' | 'rules' | 'routing' | 'reminders';
export const SETTINGS_SECTIONS: readonly SettingsSection[] = ['policy', 'rules', 'routing', 'reminders'];

/** One band in force: a rule that is on, in words. */
export interface RoutingBand { id: string; text: string }

export interface RoutingSummary {
  /** The rules that are on, each as "{name}: {band}, {who}"; empty when no rule routes. */
  bands: RoutingBand[];
  /** How many stored rules are switched off (they do not route). */
  off: number;
  /** The policy switches' effect in words; empty when the policy could not be read. */
  policy: string[];
}

/** What the Routing section says, from the rules and the policy. */
export function routingSummary(rules: readonly ApprovalRule[], policy: WorkspaceApprovalPolicy | null): RoutingSummary {
  const enabled = rules.filter((rule) => rule.enabled);
  const bands = enabled.map((rule) => ({
    id: rule.id,
    text: t('approvalSettings.routing.band', { name: rule.name, band: ruleBand(rule.conditions) ?? t('approvalSettings.rules.everyAmount'), who: ruleSteps(rule).join(t('common.listSeparator')) }, { isolate: false }),
  }));
  const policyLines = policy === null ? [] : [
    t(policy.twoPersonRule ? 'approvalSettings.routing.twoPersonOn' : 'approvalSettings.routing.twoPersonOff'),
    t(policy.mediumRiskRequiresApproval ? 'approvalSettings.routing.mediumOn' : 'approvalSettings.routing.mediumOff'),
  ];
  return { bands, off: rules.length - enabled.length, policy: policyLines };
}

/** "{count} rules, {enabled} on" for the Rules section header. */
export const rulesCount = (rules: readonly ApprovalRule[]): string =>
  t('approvalSettings.rules.count', { count: rules.length, enabled: rules.filter((rule) => rule.enabled).length });

/** The section a settings hash opens: `#approvalSettings/rules` opens Rules; anything else the top. */
export function sectionFromHash(hash: string): SettingsSection | null {
  const part = hash.replace(/^#/, '').split('/')[1];
  return (SETTINGS_SECTIONS as readonly string[]).includes(part ?? '') ? (part as SettingsSection) : null;
}
