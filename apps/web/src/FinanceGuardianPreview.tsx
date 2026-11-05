// Guardian rules preview (docs/finance.md#guardian), for Safe to spend and other Finance pages: the
// person's rules with their real on or paused state and how many flagged payments wait, from the API.
// It only reads; changing a rule happens on the Guardian page, where every change is confirmed.
import { useCallback, useEffect, useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import type { FinanceGuardian, FinanceGuardianReviewQueue } from '@anum/contracts';
import * as api from './lib/financeApi';
import { ruleKindLabel, type FinanceTab } from './lib/finance';
import { configuredKinds, isPaused, thresholdLines } from './lib/financeGuardian';
import { t, type MessageKey } from './lib/i18n';
import { Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import { FinCard, StatusChip } from './FinanceShell';
import './financeGuardian.css';

/** Up to `max` rules (in the evaluator's order), each with its setting and Enabled or Paused. */
export function GuardianRulesPreview({ onOpen, max = 3 }: { onOpen: (tab: FinanceTab) => void; max?: number }) {
  const [state, setState] = useState<Load<{ guardian: FinanceGuardian; queue: FinanceGuardianReviewQueue | null }>>({ kind: 'loading' });
  const load = useCallback(async () => {
    try {
      const [guardian, queue] = await Promise.all([api.getGuardian(), api.guardianReview({ limit: 1 }).catch(() => null)]);
      setState({ kind: 'ready', value: { guardian, queue } });
    } catch (e) { setState(failed(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const open = <button type="button" onClick={() => onOpen('guardian')}><ShieldCheck aria-hidden="true" />{t('fguard.preview.open')}</button>;
  return <FinCard className="fgPreview" icon={<ShieldCheck />} title={t('fguard.preview.title')} actions={state.kind === 'ready' ? open : undefined}>
    <p className="muted">{t('fguard.preview.text')}</p>
    {state.kind === 'loading' && <Notice busy>{t('fguard.loading')}</Notice>}
    {state.kind === 'denied' && <p className="muted">{t('finance.denied.text')}</p>}
    {state.kind === 'error' && <LoadError message={t('fguard.error', { message: state.message })} retry={() => void load()} />}
    {state.kind === 'ready' && (() => {
      const rules = state.value.guardian.rules;
      const kinds = configuredKinds(rules);
      if (kinds.length === 0) {
        return <Empty icon={<ShieldCheck aria-hidden="true" />} title={t('finance.guardian.empty')} text={t('finance.guardian.emptyText')}
          action={<button type="button" className="secondary" onClick={() => onOpen('guardian')}>{t('finance.guardian.set')}</button>} />;
      }
      const awaiting = state.value.queue?.counts.awaiting;
      return <>
        <ul className="fgPreviewList" aria-label={t('fguard.preview.list')}>{kinds.slice(0, max).map((kind) => {
          const paused = isPaused(rules, kind);
          return <li key={kind}>
            <ShieldCheck aria-hidden="true" className="fgRuleIcon" />
            <div><strong>{ruleKindLabel(kind)}</strong><small className="muted">{thresholdLines(rules, kind).slice(0, 2).join(' · ') || t(`fguard.when.${kind}` as MessageKey)}</small></div>
            <StatusChip tone={paused ? 'muted' : 'ok'}>{paused ? t('fguard.rule.paused') : t('fguard.rule.enabled')}</StatusChip>
          </li>;
        })}</ul>
        {awaiting !== undefined && <p className="muted">{t('fguard.preview.awaiting', { count: awaiting })}</p>}
        <div className="actions"><button type="button" className="secondary" onClick={() => onOpen('guardian')}>{t('fguard.preview.review')}</button></div>
      </>;
    })()}
  </FinCard>;
}
