// Policy pack editor and version comparison (docs/governance-and-scale.md#policy-packs).
// Publishing always creates a new version: the API archives the pack's current version and
// activates the new one, so nothing is edited in place and the history stays complete.
import { useId, useState } from 'react';
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react';
import { Modal } from './Dialog';
import {
  POLICY_EFFECTS, baselinePackDraft, diffRules, emptyPackDraft, emptyRuleDraft, moveRule, packBody, ruleLabel,
  type PackDraft, type PolicyEffect, type PolicyPack, type RuleDraft,
} from './lib/governance';
import * as gov from './lib/governanceApi';
import { t } from './lib/i18n';

type Act = (key: string, action: () => Promise<string>) => Promise<boolean>;

/** `basis` is the version a new version starts from (null: a brand-new pack). */
export function PackEditor({ basis, nextVersion, busy, act, onClose }: { basis: PolicyPack | null; nextVersion: number; busy: string | null; act: Act; onClose: () => void }) {
  const [draft, setDraft] = useState<PackDraft>(() => basis ? { name: basis.name, description: basis.description, rules: packRules(basis) } : emptyPackDraft());
  const [start, setStart] = useState<'blank' | 'baseline'>('blank');
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const setRule = (index: number, patch: Partial<RuleDraft>) => setDraft({ ...draft, rules: draft.rules.map((rule, i) => i === index ? { ...rule, ...patch } : rule) });
  const startFrom = (value: 'blank' | 'baseline') => { setStart(value); setDraft(value === 'baseline' ? baselinePackDraft() : emptyPackDraft()); setError(null); };
  const publish = async () => {
    const body = packBody(draft);
    if (!body.ok) { setError(body.error); return; }
    setError(null);
    const done = await act('pack', async () => {
      const pack = await gov.createPolicyPack(body.value);
      return t('gov.editor.published', { name: pack.name, version: pack.version });
    });
    if (done) onClose();
  };
  return <Modal title={basis ? t('gov.editor.titleVersion', { name: basis.name }) : t('gov.editor.title')} className="formDialog packDialog" onClose={onClose}>
    <form className="govForm packForm" aria-label={t('gov.editor.title')} noValidate onSubmit={(e) => { e.preventDefault(); void publish(); }}>
      <p className="muted">{t('gov.editor.intro')}</p>
      {!basis && <label className="field" htmlFor={`${id}-start`}><span>{t('gov.editor.startFrom')}</span>
        <select id={`${id}-start`} value={start} onChange={(e) => startFrom(e.target.value as 'blank' | 'baseline')}>
          <option value="blank">{t('gov.editor.start.blank')}</option>
          <option value="baseline">{t('gov.editor.start.baseline')}</option>
        </select></label>}
      <label className="field" htmlFor={`${id}-name`}><span>{t('gov.editor.name')}</span>
        <input id={`${id}-name`} dir="auto" value={draft.name} maxLength={160} disabled={Boolean(basis)} aria-describedby={`${id}-name-hint`} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        <small className="muted" id={`${id}-name-hint`}>{t('gov.editor.nameHint')}</small></label>
      <label className="field" htmlFor={`${id}-description`}><span>{t('gov.editor.description')}</span>
        <textarea id={`${id}-description`} dir="auto" rows={2} maxLength={1000} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label>
      <fieldset className="packRules">
        <legend>{t('gov.editor.rules')}</legend>
        <p className="muted" id={`${id}-rules-hint`}>{t('gov.editor.rulesHint')}</p>
        <div className="dataTableWrap"><table className="dataTable packRuleTable" aria-describedby={`${id}-rules-hint`}>
          <thead><tr><th scope="col" className="packRuleIndex"><span className="visuallyHidden">#</span></th><th scope="col">{t('gov.editor.col.action')}</th><th scope="col">{t('gov.editor.col.effect')}</th><th scope="col">{t('gov.editor.col.conditions')}</th><th scope="col"><span className="visuallyHidden">{t('pages2.assistants.col.actions')}</span></th></tr></thead>
          <tbody>{draft.rules.map((rule, index) => {
            const row = index + 1;
            return <tr key={index}>
              <td className="packRuleIndex num">{row}</td>
              <td><input dir="ltr" value={rule.action} maxLength={160} aria-label={t('gov.editor.actionFor', { row })} placeholder="integration.*.write" spellCheck={false} onChange={(e) => setRule(index, { action: e.target.value })} /></td>
              <td><select value={rule.effect} aria-label={t('gov.editor.effectFor', { row })} onChange={(e) => setRule(index, { effect: e.target.value as PolicyEffect })}>{POLICY_EFFECTS.map((effect) => <option key={effect} value={effect}>{t(`gov.effect.${effect}`)}</option>)}</select></td>
              <td><input dir="ltr" value={rule.conditions} aria-label={t('gov.editor.conditionsFor', { row })} placeholder='{"risk": "high"}' spellCheck={false} onChange={(e) => setRule(index, { conditions: e.target.value })} /></td>
              <td><div className="cellActions">
                <button type="button" className="iconButton small" disabled={index === 0} aria-label={t('gov.editor.moveUp', { row })} onClick={() => setDraft({ ...draft, rules: moveRule(draft.rules, index, -1) })}><ArrowUp aria-hidden="true" /></button>
                <button type="button" className="iconButton small" disabled={index === draft.rules.length - 1} aria-label={t('gov.editor.moveDown', { row })} onClick={() => setDraft({ ...draft, rules: moveRule(draft.rules, index, 1) })}><ArrowDown aria-hidden="true" /></button>
                <button type="button" className="iconButton small" disabled={draft.rules.length === 1} aria-label={t('gov.editor.removeRule', { row })} onClick={() => setDraft({ ...draft, rules: draft.rules.filter((_, i) => i !== index) })}><Trash2 aria-hidden="true" /></button>
              </div></td>
            </tr>;
          })}</tbody>
        </table></div>
        <div className="actions packAddRule"><button type="button" className="secondary" disabled={draft.rules.length >= 100} onClick={() => setDraft({ ...draft, rules: [...draft.rules, emptyRuleDraft()] })}><Plus size={16} aria-hidden="true" />{t('gov.editor.addRule')}</button></div>
      </fieldset>
      {error && <p className="notice errorNotice" role="alert">{error}</p>}
      <div className="actions">
        <button type="submit" disabled={busy !== null}>{basis ? t('gov.editor.publishVersion', { version: nextVersion }) : t('gov.editor.publish')}</button>
        <button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button>
      </div>
    </form>
  </Modal>;
}

function packRules(pack: PolicyPack): RuleDraft[] {
  return pack.rules.map((rule) => ({
    action: rule.action,
    effect: (POLICY_EFFECTS as readonly string[]).includes(rule.effect) ? rule.effect as PolicyEffect : 'require_approval',
    conditions: rule.conditions && Object.keys(rule.conditions).length ? JSON.stringify(rule.conditions) : '',
  }));
}

/** Two versions of one pack side by side: which rules were added, removed or changed. */
export function CompareVersions({ name, versions, onClose }: { name: string; versions: PolicyPack[]; onClose: () => void }) {
  const [fromId, setFromId] = useState(versions[Math.min(1, versions.length - 1)].id);
  const [toId, setToId] = useState(versions[0].id);
  const id = useId();
  const from = versions.find((v) => v.id === fromId) ?? versions[0];
  const to = versions.find((v) => v.id === toId) ?? versions[0];
  const changes = diffRules(from.rules, to.rules);
  const pick = (key: 'from' | 'to', value: string, set: (v: string) => void) => <label className="field" htmlFor={`${id}-${key}`}><span>{t(key === 'from' ? 'gov.packs.compareFrom' : 'gov.packs.compareTo')}</span>
    <select id={`${id}-${key}`} value={value} onChange={(e) => set(e.target.value)}>{versions.map((v) => <option key={v.id} value={v.id}>{t('gov.packs.version', { version: v.version })}{v.active ? ` (${t('gov.packs.active')})` : ''}</option>)}</select></label>;
  return <Modal title={t('gov.packs.compareTitle', { name })} className="formDialog packDialog" onClose={onClose}>
    <div className="govGrid two">{pick('from', fromId, setFromId)}{pick('to', toId, setToId)}</div>
    {changes.every((change) => change.kind === 'same') && <p className="muted">{t('gov.packs.noChanges')}</p>}
    <ul className="packDiff" aria-label={t('gov.packs.compareTitle', { name })}>
      {changes.map((change) => <li key={`${change.kind}-${change.action}`} className={`packDiff-${change.kind}`}>
        <span className={`statusBadge ${change.kind === 'added' ? 'success' : change.kind === 'removed' ? 'dangerTone' : change.kind === 'changed' ? 'warning' : 'neutral'}`}>{t(`gov.packs.change.${change.kind}`)}</span>
        <span className="packDiffRule" dir="ltr">
          {change.kind === 'changed' && change.before && <><s>{ruleLabel(change.before)}</s><br /></>}
          {ruleLabel((change.after ?? change.before)!)}
        </span>
      </li>)}
    </ul>
    <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('gov.close')}</button></div>
  </Modal>;
}
