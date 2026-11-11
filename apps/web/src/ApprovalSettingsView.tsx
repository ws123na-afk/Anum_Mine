// Approvals settings (docs/approvals-and-risk.md#approvals-settings): one page with four sections, each
// with its own load, save and permission state. Policy (the two workspace switches, owners save),
// Rules (the organization's approval rules, read here and changed on Governance by an organization
// administrator), Routing (how a pausing action finds its approvers, read from the rules and the
// policy) and Reminders (the approval-ageing reminder). `#policy` still opens this page.
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Building2, CircleDot, ExternalLink, Lock, RefreshCw, RotateCcw, Route, Save, Scale } from 'lucide-react';
import type { WorkspaceApprovalPolicy, WorkspaceMember, WorkspaceRole } from '@anum/contracts';
import * as admin from './lib/adminApi';
import * as gov from './lib/governanceApi';
import { ROLES } from './lib/admin';
import { getCurrentMembership } from './lib/api';
import { SETTINGS_SECTIONS, routingSummary, rulesCount, sectionFromHash, type SettingsSection } from './lib/approvalSettings';
import { ruleBand, ruleSteps, type ApprovalRule } from './lib/approvalRules';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { POLICY_SWITCHES, policyChanged, policyDraft, policyUpdatedLabel, twoPersonWarning, type PolicyDraft } from './lib/policy';
import { ApprovalReminderCard } from './ApprovalReminderCard';
import './admin.css';
import './governance.css';
import './approvalSettings.css';

type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'forbidden'; detail: string } | { kind: 'error'; detail: string };
type Flash = { text: string; error?: boolean } | null;
const when = (value: string) => formatDateTime(value);

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

function Forbidden({ title, detail, children }: { title: string; detail: string | null; children: ReactNode }) {
  return <section className="surface adminForbidden" aria-label={title}>
    <span className="adminIcon" aria-hidden="true"><Lock /></span>
    <div><h3>{title}</h3><p>{children}</p>{detail && <p className="muted">{t('admin.apiAnswered', { detail })}</p>}</div>
  </section>;
}

/** The page: the four sections in order, with an in-page index that scrolls to each. */
export function ApprovalSettingsView({ workspaceId, onOpenGovernance }: { workspaceId: string; onOpenGovernance: () => void }) {
  const [policy, setPolicy] = useState<WorkspaceApprovalPolicy | null>(null);
  const [draft, setDraft] = useState<PolicyDraft>({ twoPersonRule: false, mediumRiskRequiresApproval: false });
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [role, setRole] = useState<string | null>(null);
  const [members, setMembers] = useState<WorkspaceMember[] | null>(null);
  const [denied, setDenied] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  const [rules, setRules] = useState<{ kind: 'loading' } | { kind: 'ready'; rules: ApprovalRule[] } | { kind: 'error'; detail: string }>({ kind: 'loading' });

  const refresh = useCallback(async () => {
    try {
      const [next, membership] = await Promise.all([admin.getApprovalPolicy(), getCurrentMembership().catch(() => null)]);
      setPolicy(next);
      setDraft(policyDraft(next));
      const ownRole = membership?.active ? membership.role : null;
      setRole(ownRole);
      // Owners can list members: used only to warn before the two-person rule locks everyone out.
      setMembers(ownRole === 'owner' ? await admin.getMembers().catch(() => null) : null);
      setLoad({ kind: 'ready' });
    } catch (error) {
      setLoad(isPermissionDenied(error) ? { kind: 'forbidden', detail: describeError(error) } : { kind: 'error', detail: describeError(error) });
    }
  }, []);
  const loadRules = useCallback(async () => {
    setRules({ kind: 'loading' });
    try { setRules({ kind: 'ready', rules: await gov.getApprovalRules() }); }
    catch (error) { setRules({ kind: 'error', detail: describeError(error) }); }
  }, []);
  useEffect(() => { void refresh(); void loadRules(); }, [refresh, loadRules]);
  // A deep link such as #approvalSettings/rules scrolls to its section once it is on the page.
  useEffect(() => {
    const section = sectionFromHash(window.location.hash);
    if (section && load.kind === 'ready') document.getElementById(`approval-${section}`)?.scrollIntoView({ block: 'start' });
  }, [load.kind]);

  const notOwner = role !== null && role !== 'owner';
  const readOnly = notOwner || denied !== null;
  const changed = policy !== null && policyChanged(policy, draft);
  const warning = twoPersonWarning(draft, members);

  const save = () => {
    setBusy(true);
    setFlash(null);
    void admin.setApprovalPolicy(draft).then((next) => {
      setPolicy(next);
      setDraft(policyDraft(next));
      setFlash({ text: t('policy.saved') });
    }).catch((error: unknown) => {
      if (isPermissionDenied(error)) {
        setDenied(describeError(error));
        if (policy) setDraft(policyDraft(policy));
      } else {
        setFlash({ text: describeError(error), error: true });
      }
    }).finally(() => setBusy(false));
  };

  const jump = (section: SettingsSection) => {
    history.replaceState(null, '', `#approvalSettings/${section}`);
    document.getElementById(`approval-${section}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    document.getElementById(`approval-${section}`)?.focus({ preventScroll: true });
  };

  return <>
    <section className="moduleIntro">
      {/* The page title is the shell's h1 (Approvals settings): the intro only says what the page holds. */}
      <div><p>{t('approvalSettings.intro', { workspace: workspaceId })}</p></div>
      <button type="button" onClick={() => { setLoad({ kind: 'loading' }); setDenied(null); setFlash(null); void refresh(); void loadRules(); }}><RefreshCw aria-hidden="true" />{t('admin.refresh')}</button>
    </section>
    <nav className="approvalSections" aria-label={t('approvalSettings.sections')}>
      {SETTINGS_SECTIONS.map((section) => <a key={section} href={`#approvalSettings/${section}`} onClick={(event) => { event.preventDefault(); jump(section); }}>{t(`approvalSettings.section.${section}` as MessageKey)}</a>)}
    </nav>
    {load.kind === 'loading' && <Notice>{t('policy.loading')}</Notice>}
    {load.kind === 'error' && <Notice error>{t('policy.error', { message: load.detail })}</Notice>}
    {load.kind === 'forbidden' && <Forbidden title={t('policy.accessRequired')} detail={load.detail}>
      {t('policy.forbidden')}
    </Forbidden>}
    {load.kind === 'ready' && policy && <>
      {readOnly && <Forbidden title={t('admin.ownerRequired')} detail={denied}>
        {role && role !== 'owner' ? t('policy.readOnlyRole', { role: (ROLES as readonly string[]).includes(role) ? t(`members.role.${role as WorkspaceRole}`) : role }) : t('policy.readOnly')}
      </Forbidden>}
      <section className="surface adminPanel policyPanel" id="approval-policy" tabIndex={-1} aria-label={t('policy.region')}>
        <div className="sectionHeader"><div><p className="eyebrow">{t('policy.inForce')}</p><h2>{t('policy.title')}</h2></div><Scale className="sectionIcon" aria-hidden="true" /></div>
        <p className="muted">{t('policy.help')}</p>
        {POLICY_SWITCHES.map((item) => {
          const on = draft[item.key];
          return <div className={`policySwitch ${on ? 'on' : ''}`} key={item.key}>
            <label>
              <span className="policySwitchTitle"><strong>{item.title}</strong><span className={`statusBadge ${on ? 'success' : 'neutral'}`}>{on ? t('policy.on') : t('policy.off')}</span></span>
              <input type="checkbox" role="switch" aria-label={item.title} aria-describedby={`policy-${item.key}`} checked={on} disabled={readOnly || busy} onChange={(event) => setDraft({ ...draft, [item.key]: event.target.checked })} />
            </label>
            <ul id={`policy-${item.key}`} className="policyExplain">
              <li className={on ? 'active' : ''}><span>{t('policy.whenOn')}</span>{item.on}</li>
              <li className={on ? '' : 'active'}><span>{t('policy.whenOff')}</span>{item.off}</li>
            </ul>
          </div>;
        })}
        {warning && <Notice error>{warning}</Notice>}
        <p className="muted">{policyUpdatedLabel(policy, when)}</p>
        {flash && <Notice error={flash.error}>{flash.text}</Notice>}
        {!readOnly && <div className="actions">
          <button type="button" disabled={busy || !changed} onClick={save}><Save aria-hidden="true" />{busy ? t('admin.saving') : t('policy.save')}</button>
          {changed && <button type="button" className="secondary" disabled={busy} onClick={() => setDraft(policyDraft(policy))}><RotateCcw aria-hidden="true" />{t('policy.undo')}</button>}
        </div>}
      </section>
      <RulesSection state={rules} onRetry={() => void loadRules()} onOpenGovernance={onOpenGovernance} />
      {/* The routing reads the switches as drafted, so an owner sees the effect before saving. */}
      <RoutingSection rules={rules.kind === 'ready' ? rules.rules : null} policy={{ ...policy, ...draft }} />
      <div id="approval-reminders" tabIndex={-1}><ApprovalReminderCard readOnly={readOnly} /></div>
    </>}
  </>;
}

/** The organization's rules, read only here: the Governance page edits them. */
function RulesSection({ state, onRetry, onOpenGovernance }: { state: { kind: 'loading' } | { kind: 'ready'; rules: ApprovalRule[] } | { kind: 'error'; detail: string }; onRetry: () => void; onOpenGovernance: () => void }) {
  return <section className="surface adminPanel policyPanel approvalRulesSection" id="approval-rules" tabIndex={-1} aria-label={t('approvalSettings.rules.title')}>
    <div className="sectionHeader"><div><p className="eyebrow">{t('approvalSettings.rules.eyebrow')}</p><h2>{t('approvalSettings.rules.title')}</h2></div><Building2 className="sectionIcon" aria-hidden="true" /></div>
    <p className="muted">{t('approvalSettings.rules.intro')}</p>
    {state.kind === 'loading' && <Notice>{t('approvalSettings.rules.loading')}</Notice>}
    {state.kind === 'error' && <div className="actions"><Notice error>{t('approvalSettings.rules.error', { message: state.detail })}</Notice><button type="button" className="secondary" onClick={onRetry}><RefreshCw aria-hidden="true" />{t('common.retry')}</button></div>}
    {state.kind === 'ready' && <>
      {state.rules.length === 0 ? <p className="muted">{t('approvalSettings.rules.empty')}</p> : <>
        <p className="muted">{rulesCount(state.rules)}</p>
        <ul className="ruleList approvalRuleList">
          {state.rules.map((rule) => <li className="ruleRow" key={rule.id}>
            <Scale aria-hidden="true" />
            <div className="ruleMain">
              <strong dir="auto">{rule.name}</strong> <span className={`statusBadge ${rule.enabled ? 'success' : 'neutral'}`}>{rule.enabled ? t('rules.on') : t('rules.off')}</span>
              <p>{t('approvalSettings.rules.pattern', { pattern: rule.action_pattern })} · {ruleBand(rule.conditions) ?? t('approvalSettings.rules.everyAmount')}</p>
              <div className="tagRow">{ruleSteps(rule).map((step) => <span key={step} dir="auto">{step}</span>)}</div>
            </div>
          </li>)}
        </ul>
      </>}
      <p className="muted">{t('approvalSettings.rules.readOnly')}</p>
      <div className="actions"><button type="button" className="secondary" onClick={onOpenGovernance}><ExternalLink aria-hidden="true" />{t('approvalSettings.rules.manage')}</button></div>
    </>}
  </section>;
}

/** How a pausing action is routed, in the API's order, with the bands and switches in force. */
function RoutingSection({ rules, policy }: { rules: ApprovalRule[] | null; policy: WorkspaceApprovalPolicy | null }) {
  const summary = routingSummary(rules ?? [], policy);
  const steps: MessageKey[] = ['approvalSettings.routing.step.pause', 'approvalSettings.routing.step.match', 'approvalSettings.routing.step.band', 'approvalSettings.routing.step.fallback', 'approvalSettings.routing.step.owners'];
  return <section className="surface adminPanel policyPanel approvalRoutingSection" id="approval-routing" tabIndex={-1} aria-label={t('approvalSettings.routing.title')}>
    <div className="sectionHeader"><div><p className="eyebrow">{t('approvalSettings.routing.eyebrow')}</p><h2>{t('approvalSettings.routing.title')}</h2></div><Route className="sectionIcon" aria-hidden="true" /></div>
    <p className="muted">{t('approvalSettings.routing.intro')}</p>
    <ol className="approvalRoutingSteps">{steps.map((key) => <li key={key}>{t(key)}</li>)}</ol>
    <h3 className="approvalRoutingHeading">{t('approvalSettings.routing.bands')}</h3>
    {rules === null ? <Notice>{t('approvalSettings.rules.loading')}</Notice>
      : summary.bands.length === 0 ? <p className="muted">{t('approvalSettings.routing.noBands')}</p>
        : <ul className="approvalRoutingBands">{summary.bands.map((band) => <li key={band.id} dir="auto">{band.text}</li>)}</ul>}
    {summary.off > 0 && <p className="muted">{t('approvalSettings.routing.off', { count: summary.off })}</p>}
    {summary.policy.length ? <ul className="approvalRoutingPolicy">{summary.policy.map((line) => <li key={line}>{line}</li>)}</ul> : <p className="muted">{t('approvalSettings.routing.unknown')}</p>}
  </section>;
}
