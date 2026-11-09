// "Apply to similar" from the Transactions drawer (docs/finance.md#rules-and-learning): after the owner corrects a row,
// the rule the correction suggests and a preview of the other rows it would change. Saving the rule, approving it and
// applying it to earlier rows are explicit steps; applying is one batch that can be undone.
import { useCallback, useEffect, useState } from 'react';
import type { FinanceSimilar } from '@anum/contracts';
import * as rx from './lib/financeRulesApi';
import { categoryLabel } from './lib/finance';
import { matchSummary } from './lib/financeSuggestions';
import { describeError } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { Modal } from './Dialog';
import { Denied, LoadError, Notice, failed, type Load } from './FinanceParts';
import { PreviewBlock } from './FinanceSuggestions';

export function SimilarDialog({ transactionId, onClose, onDone }: { transactionId: string; onClose: () => void; onDone: (text: string, batchId?: string) => void }) {
  const [scope, setScope] = useState<'all' | 'account'>('all');
  const [state, setState] = useState<Load<FinanceSimilar>>({ kind: 'loading' });
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setState({ kind: 'loading' });
    rx.similar(transactionId, scope).then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e)));
  }, [transactionId, scope]);
  useEffect(() => { load(); }, [load]);
  const save = async (similar: FinanceSimilar, apply: boolean) => {
    setBusy(true); setError(null);
    try {
      const rule = await rx.saveAndApprove(similar.content, { sourceTransactionId: similar.source_transaction_id });
      if (!apply) { onDone(t('fsug.similar.savedOnly')); return; }
      const preview = await rx.previewRule(rule.rule_id);
      if (!preview.changes) { onDone(t('fsug.similar.savedOnly')); return; }
      const batch = await rx.applyRule(rule.rule_id, preview.preview_hash);
      onDone(t('fsug.preview.applied', { count: batch.changed }), batch.id);
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <Modal title={t('fsug.similar.title')} className="fsugSimilar" onClose={onClose}>
    <div className="finControl"><span className="finControlLabel" id={`sim-scope-${transactionId}`}>{t('fsug.similar.scope')}</span>
      <div className="finSegmented" role="radiogroup" aria-labelledby={`sim-scope-${transactionId}`}>
        {(['all', 'account'] as const).map((s) => <button key={s} type="button" role="radio" aria-checked={scope === s} disabled={busy} onClick={() => setScope(s)}>
          {t(`fsug.similar.scope.${s}` as MessageKey)}</button>)}
      </div></div>
    {state.kind === 'loading' && <Notice busy>{t('fsug.loading')}</Notice>}
    {state.kind === 'denied' && <Denied />}
    {state.kind === 'error' && <LoadError message={state.message} retry={load} />}
    {state.kind === 'ready' && <>
      <p dir="auto">{t('fsug.similar.rule', { match: matchSummary(state.value.content.match).join(', '),
        direction: t(`fsug.dir.${state.value.content.match.direction ?? 'any'}` as MessageKey), category: categoryLabel(state.value.content.category) })}</p>
      <PreviewBlock preview={state.value.preview} />
    </>}
    {error && <Notice error>{error}</Notice>}
    <div className="actions">
      {state.kind === 'ready' && state.value.preview.changes > 0 && <button type="button" disabled={busy} onClick={() => void save(state.value, true)}>
        {busy ? t('finance.saving') : t('fsug.similar.saveApply', { count: state.value.preview.changes })}</button>}
      {state.kind === 'ready' && <button type="button" className="secondary" disabled={busy} onClick={() => void save(state.value, false)}>{t('fsug.similar.saveOnly')}</button>}
      <button type="button" className="secondary" disabled={busy} onClick={onClose}>{t('common.cancel')}</button>
    </div>
  </Modal>;
}
