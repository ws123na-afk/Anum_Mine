// Organization approval rules on the Governance page: AED bands, a share of the order value, the
// disputed flag, and who approves in one or two steps (docs/approvals-and-risk.md#approval-routing).
// Organization administrators add, edit, turn off and delete rules; everyone else reads them.
import { useId, useState } from 'react';
import { Pencil, Plus, Power, Scale, Trash2 } from 'lucide-react';
import { ConfirmDialog } from './Dialog';
import {
  draftFromRule, emptyRuleDraft, ruleBand, ruleBody, ruleSteps, type ApprovalRule, type ApproverDraft, type Disputed, type RuleDraft,
} from './lib/approvalRules';
import * as gov from './lib/governanceApi';
import { t } from './lib/i18n';

type Act = (key: string, action: () => Promise<string>) => Promise<boolean>;

export function ApprovalRulesPanel({ rules, canManage, busy, act }: { rules: ApprovalRule[]; canManage: boolean; busy: string | null; act: Act }) {
  const [editing, setEditing] = useState<{ id: string | null; draft: RuleDraft } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<ApprovalRule | null>(null);

  const save = async () => {
    if (!editing) return;
    const body = ruleBody(editing.draft);
    if (!body.ok) { setError(body.error); return; }
    setError(null);
    const done = await act('approval-rule', async () => {
      const saved = editing.id ? await gov.updateApprovalRule(editing.id, body.value) : await gov.createApprovalRule(body.value);
      return t(editing.id ? 'rules.saved' : 'rules.created', { name: saved.name });
    });
    if (done) setEditing(null);
  };
  const toggle = (rule: ApprovalRule) => act(`toggle-${rule.id}`, async () => {
    const draft = { ...draftFromRule(rule), enabled: !rule.enabled };
    const body = ruleBody(draft);
    if (!body.ok) throw new Error(body.error);
    const saved = await gov.updateApprovalRule(rule.id, rule.approver_steps?.length ? body.value : { ...body.value, approver_steps: [], minimum_approvers: rule.minimum_approvers, required_roles: rule.required_roles, conditions: rule.conditions ?? {} });
    return t(saved.enabled ? 'rules.turnedOn' : 'rules.turnedOff', { name: saved.name });
  });
  const remove = async () => {
    if (!deleting) return;
    const rule = deleting;
    if (await act(`delete-${rule.id}`, async () => { await gov.deleteApprovalRule(rule.id); return t('rules.deleted', { name: rule.name }); })) setDeleting(null);
  };

  return <section className="surface govSection" aria-label={t('rules.title')}>
    <div className="sectionHeader">
      <div><p className="eyebrow">{t('rules.eyebrow')}</p><h2>{t('rules.title')}</h2><p className="muted">{t('rules.intro')}</p></div>
      {canManage && !editing && <button type="button" disabled={busy !== null} onClick={() => { setError(null); setEditing({ id: null, draft: emptyRuleDraft() }); }}><Plus size={16} aria-hidden="true" />{t('rules.add')}</button>}
    </div>
    {editing && canManage && <RuleForm editing={editing} error={error} busy={busy} onChange={(draft) => setEditing({ ...editing, draft })} onSave={() => void save()} onCancel={() => { setEditing(null); setError(null); }} />}
    {rules.length === 0 && <p className="muted">{t('rules.empty')}</p>}
    <ul className="ruleList">
      {rules.map((rule) => {
        const band = ruleBand(rule.conditions);
        return <li className="ruleRow" key={rule.id}>
          <Scale aria-hidden="true" />
          <div className="ruleMain">
            <strong dir="auto">{rule.name}</strong> <span className={`statusBadge ${rule.enabled ? 'success' : 'neutral'}`}>{rule.enabled ? t('rules.on') : t('rules.off')}</span>
            <p>{t('rules.pattern', { pattern: rule.action_pattern })} · {band ?? t('rules.everyAmount')}</p>
            <div className="tagRow">{ruleSteps(rule).map((step) => <span key={step} dir="auto">{step}</span>)}</div>
          </div>
          {canManage && <div className="ruleActions">
            <button type="button" className="secondary" disabled={busy !== null} aria-label={t('rules.editLabel', { name: rule.name }, { isolate: false })} onClick={() => { setError(null); setEditing({ id: rule.id, draft: draftFromRule(rule) }); }}><Pencil size={16} aria-hidden="true" />{t('rules.edit')}</button>
            <button type="button" className="secondary" disabled={busy !== null} aria-label={t(rule.enabled ? 'rules.turnOffLabel' : 'rules.turnOnLabel', { name: rule.name }, { isolate: false })} onClick={() => void toggle(rule)}><Power size={16} aria-hidden="true" />{rule.enabled ? t('rules.turnOff') : t('rules.turnOn')}</button>
            <button type="button" className="secondary" disabled={busy !== null} aria-label={t('rules.deleteLabel', { name: rule.name }, { isolate: false })} onClick={() => setDeleting(rule)}><Trash2 size={16} aria-hidden="true" />{t('rules.delete')}</button>
          </div>}
        </li>;
      })}
    </ul>
    {deleting && <ConfirmDialog title={t('rules.deleteTitle', { name: deleting.name })} confirmLabel={t('rules.delete')} danger busy={busy !== null} onConfirm={() => void remove()} onCancel={() => setDeleting(null)}>
      <p>{t('rules.deleteBody')}</p>
    </ConfirmDialog>}
  </section>;
}

function RuleForm({ editing, error, busy, onChange, onSave, onCancel }: {
  editing: { id: string | null; draft: RuleDraft }; error: string | null; busy: string | null;
  onChange: (draft: RuleDraft) => void; onSave: () => void; onCancel: () => void;
}) {
  const { draft } = editing;
  const id = useId();
  const set = <K extends keyof RuleDraft>(key: K, value: RuleDraft[K]) => onChange({ ...draft, [key]: value });
  const input = (key: 'name' | 'actionPattern' | 'amountAbove' | 'amountAtMost' | 'percentAtMost' | 'amountField' | 'orderValueField', label: string, opts: { dir?: 'ltr' | 'auto'; hint?: string; inputMode?: 'decimal' } = {}) =>
    <label className="field" htmlFor={`${id}-${key}`}><span>{label}</span>
      <input id={`${id}-${key}`} dir={opts.dir ?? 'ltr'} inputMode={opts.inputMode} value={draft[key]} aria-describedby={opts.hint ? `${id}-${key}-hint` : undefined} onChange={(e) => set(key, e.target.value)} />
      {opts.hint && <small className="muted" id={`${id}-${key}-hint`}>{opts.hint}</small>}
    </label>;
  return <form className="govForm ruleForm" aria-label={t('rules.form.label')} noValidate onSubmit={(e) => { e.preventDefault(); onSave(); }}>
    <h3>{editing.id ? t('rules.form.edit', { name: draft.name }) : t('rules.form.new')}</h3>
    <div className="ruleGrid">
      {input('name', t('rules.field.name'), { dir: 'auto' })}
      {input('actionPattern', t('rules.field.pattern'), { hint: t('rules.field.patternHint') })}
    </div>
    <fieldset className="ruleFieldset">
      <legend>{t('rules.field.band')}</legend>
      <div className="ruleGrid three">
        {input('amountAbove', t('rules.field.amountAbove'), { inputMode: 'decimal' })}
        {input('amountAtMost', t('rules.field.amountAtMost'), { inputMode: 'decimal' })}
        {input('percentAtMost', t('rules.field.percent'), { inputMode: 'decimal' })}
      </div>
      <small className="muted">{t('rules.field.amountHint')}</small>
      <label className="field" htmlFor={`${id}-disputed`}><span>{t('rules.field.disputed')}</span>
        <select id={`${id}-disputed`} value={draft.disputed} onChange={(e) => set('disputed', e.target.value as Disputed)}>
          {(['any', 'yes', 'no'] as const).map((value) => <option key={value} value={value}>{t(`rules.disputed.${value}`)}</option>)}
        </select></label>
      <details className="ruleAdvanced"><summary>{t('rules.field.advanced')}</summary>
        <div className="ruleGrid">{input('amountField', t('rules.field.amountField'))}{input('orderValueField', t('rules.field.orderField'))}</div>
        <small className="muted">{t('rules.field.advancedHint')}</small>
      </details>
    </fieldset>
    <Approvers legend={t('rules.approvers.first')} value={draft.first} onChange={(first) => set('first', first)} />
    <label className="integrationToggle ruleCheck"><input type="checkbox" checked={draft.twoSteps} onChange={(e) => set('twoSteps', e.target.checked)} /><span>{t('rules.twoSteps')}</span></label>
    {draft.twoSteps && <Approvers legend={t('rules.approvers.second')} value={draft.second} onChange={(second) => set('second', second)} />}
    <label className="integrationToggle ruleCheck"><input type="checkbox" checked={draft.enabled} onChange={(e) => set('enabled', e.target.checked)} /><span>{t('rules.field.enabled')}</span></label>
    {error && <p className="notice errorNotice" role="alert">{error}</p>}
    <div className="actions">
      <button type="submit" disabled={busy !== null}>{t('rules.save')}</button>
      <button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button>
    </div>
  </form>;
}

function Approvers({ legend, value, onChange }: { legend: string; value: ApproverDraft; onChange: (value: ApproverDraft) => void }) {
  const id = useId();
  const box = (key: 'owner' | 'orgAdmin' | 'members', label: string) =>
    <label><input type="checkbox" checked={value[key]} onChange={(e) => onChange({ ...value, [key]: e.target.checked })} /><span>{label}</span></label>;
  return <fieldset className="govPermissions ruleApprovers">
    <legend>{legend}</legend>
    {box('owner', t('rules.approvers.owner'))}{box('orgAdmin', t('rules.approvers.orgAdmin'))}{box('members', t('rules.approvers.members'))}
    <label className="field rulePeople" htmlFor={`${id}-people`}><span>{t('rules.approvers.people')}</span>
      <input id={`${id}-people`} dir="ltr" value={value.people} autoComplete="off" spellCheck={false} aria-describedby={`${id}-people-hint`} onChange={(e) => onChange({ ...value, people: e.target.value })} />
      <small className="muted" id={`${id}-people-hint`}>{t('rules.approvers.peopleHint')}</small>
    </label>
  </fieldset>;
}
