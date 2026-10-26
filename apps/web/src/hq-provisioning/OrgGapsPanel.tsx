import { useState } from 'react';
import { AlertTriangle, CheckCircle2, CircleSlash, PlugZap, RefreshCcw, UserPlus } from 'lucide-react';
import type { ProvisioningCoverage, ProvisioningGap, ProvisioningReason, ProvisioningTemplateId } from '@anum/contracts';
import { t, type MessageKey } from '../lib/i18n';
import { useLocale } from '../useLocale';
import { orderedGaps } from './client';
import { ProposalDialog } from './ProposalDialog';
import type { useProvisioningGaps } from './useProvisioning';
import './hqProvisioning.css';

type GapsState = ReturnType<typeof useProvisioningGaps>;
export interface OrgGapsPanelProps {
  state: GapsState;
  /** Role names for display, by template id and locale (taken from the API catalogue the dialog loads). */
  onNavigate: (hash: string) => void;
  /** Opens "Staff my campus" (src/hq-staff-desks). The button is shown only when given. */
  onStaffCampus?: () => void;
  /** Called after an agent was created, so the live flow and the team re-read. */
  onProvisioned: () => void;
}

/** Display names of the eleven roles. The API sends them too; these keep the list readable before any proposal loads. */
export const ROLE_NAMES: Record<ProvisioningTemplateId, { en: string; ar: string }> = {
  orders_desk: { en: 'Orders Desk', ar: 'مكتب الطلبات' },
  sales_followup: { en: 'Sales Follow-up', ar: 'متابعة المبيعات' },
  support_triage: { en: 'Customer Support Triage', ar: 'فرز دعم العملاء' },
  finance_reconciliation: { en: 'Finance Reconciliation Clerk', ar: 'موظف مطابقة مالية' },
  fleet_maintenance: { en: 'Fleet Maintenance Coordinator', ar: 'منسق صيانة الأسطول' },
  operations_dispatcher: { en: 'Operations Dispatcher', ar: 'مسؤول توزيع العمليات' },
  compliance_reviewer: { en: 'Compliance Reviewer', ar: 'مراجع الامتثال' },
  marketing_specialist: { en: 'Marketing Specialist', ar: 'أخصائي التسويق' },
  technology_coordinator: { en: 'Technology Coordinator', ar: 'منسق التقنية' },
  research_analyst: { en: 'Research Analyst', ar: 'محلل أبحاث' },
  legal_reviewer: { en: 'Legal Reviewer', ar: 'مراجع قانوني' },
};

export function roleName(id: ProvisioningTemplateId, locale: 'en' | 'ar'): string {
  return ROLE_NAMES[id]?.[locale] ?? id;
}

export function reasonLine(r: ProvisioningReason): string {
  return t(`hqProv.reason.${r.code}` as MessageKey, {
    count: r.count ?? 0, stuck: r.stuck ?? 0, department: r.department ?? '',
    rooms: (r.rooms ?? []).map((room) => room.name).join(', '),
  });
}

export function OrgGapsPanel({ state, onNavigate, onStaffCampus, onProvisioned }: OrgGapsPanelProps) {
  const locale = useLocale();
  const { gaps, status, error, reload } = state;
  const [review, setReview] = useState<ProvisioningTemplateId | null>(null);
  const gapIds = new Set((gaps?.gaps ?? []).map((g) => g.template_id));
  const others = (gaps?.coverage ?? []).filter((c) => !gapIds.has(c.template_id));
  const open = (id: ProvisioningTemplateId) => setReview(id);
  return <section className="hqProv" aria-label={t('hqProv.region')}>
    <h3 className="hqProvH"><UserPlus size={15} aria-hidden="true" /> {t('hqProv.title')}
      {gaps && <span>{t('hqProv.agentsUsed', { used: gaps.limits.agents, max: gaps.limits.max_agents })}</span>}</h3>
    <p className="hqProvNote">{t('hqProv.intro')}</p>
    {onStaffCampus && <p className="hqProvNote"><button type="button" className="hqProvPrimary" onClick={onStaffCampus}><UserPlus size={14} aria-hidden="true" /> {t('hqSd.gapsEntry')}</button> {t('hqSd.gapsHint')}</p>}
    {status === 'loading' && <p className="hqProvNote" role="status">{t('hqProv.loading')}</p>}
    {status === 'denied' && <p className="hqProvNote" role="alert">{t('hqProv.denied')}</p>}
    {status === 'error' && <div className="hqProvNote" role="alert">
      <p>{t('hqProv.error', { detail: error })}</p>
      <button type="button" onClick={() => void reload()}><RefreshCcw size={14} aria-hidden="true" /> {t('hqProv.retry')}</button>
    </div>}
    {gaps && <>
      <h4 className="hqProvSub">{t('hqProv.gapsTitle')}</h4>
      {gaps.gaps.length === 0 ? <p className="hqProvNote hqProvOk"><CheckCircle2 size={14} aria-hidden="true" /> {t('hqProv.noGaps')} {t('hqProv.noGapsAction')}</p>
        : <ul className="hqProvList" aria-label={t('hqProv.gapsTitle')}>
          {orderedGaps(gaps.gaps).map((g) => <GapCard key={g.key} gap={g} locale={locale} onReview={open} />)}
        </ul>}
      <h4 className="hqProvSub">{gaps.gaps.length ? t('hqProv.coverageTitle') : t('hqProv.otherTitle')}</h4>
      <ul className="hqProvList hqProvCoverage" aria-label={t('hqProv.coverageTitle')}>
        {(gaps.gaps.length ? gaps.coverage : others).map((c) => <CoverageRow key={c.template_id} c={c} locale={locale} onReview={open}
          canConfirm={gaps.can_confirm} />)}
      </ul>
      {!gaps.can_confirm && <p className="hqProvNote">{t('hqProv.confirmOnlyOwners')}</p>}
    </>}
    {review && <ProposalDialog templateId={review} onClose={() => setReview(null)} onNavigate={onNavigate}
      onProvisioned={() => { onProvisioned(); void reload(); }} />}
  </section>;
}

function GapCard({ gap, locale, onReview }: { gap: ProvisioningGap; locale: 'en' | 'ar'; onReview: (id: ProvisioningTemplateId) => void }) {
  const role = roleName(gap.template_id, locale);
  return <li className="hqProvCard" data-severity={gap.severity}>
    <div className="hqProvCardHead">
      <strong>{role}</strong>
      <span className="hqProvBadge" data-severity={gap.severity}><AlertTriangle size={12} aria-hidden="true" /> {t(`hqProv.severity.${gap.severity}` as MessageKey)}</span>
    </div>
    <ul className="hqProvReasons">{gap.reasons.map((r, i) => <li key={i}>{reasonLine(r)}</li>)}</ul>
    <button type="button" className="hqProvPrimary" onClick={() => onReview(gap.template_id)} aria-label={t('hqProv.reviewLabel', { role })}>{t('hqProv.review')}</button>
  </li>;
}

function CoverageRow({ c, locale, onReview, canConfirm }: {
  c: ProvisioningCoverage; locale: 'en' | 'ar'; onReview: (id: ProvisioningTemplateId) => void; canConfirm: boolean;
}) {
  const role = roleName(c.template_id, locale);
  const Icon = c.state === 'covered' ? CheckCircle2 : c.state.startsWith('source_') ? PlugZap : CircleSlash;
  const hint = c.state === 'source_not_connected' || c.state === 'source_no_access' ? t(`hqProv.coverageHint.${c.state}` as MessageKey) : null;
  return <li className="hqProvRow" data-state={c.state}>
    <div>
      <strong>{role}</strong>
      <span className="hqProvState"><Icon size={13} aria-hidden="true" /> {t(`hqProv.coverage.${c.state}` as MessageKey)}</span>
      {c.handler && <small>{t('hqProv.handler', { agent: c.handler.agent_name, status: t(`hqProv.agentStatus.${c.handler.agent_status}` as MessageKey) })}</small>}
      {hint && <small>{hint}</small>}
    </div>
    {!c.handler && <button type="button" onClick={() => onReview(c.template_id)} aria-label={t('hqProv.addLabel', { role })}
      title={canConfirm ? undefined : t('hqProv.confirmOnlyOwners')}>{t('hqProv.add')}</button>}
  </li>;
}
