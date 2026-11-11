// Memory governance (docs/governance-and-scale.md#memory-governance): how long agents keep what
// they learn and from which sources. One tenant-wide setting, read from and saved to
// GET/PUT /organization/memory-governance; a 404 means nobody saved it yet.
import { useCallback, useEffect, useId, useState } from 'react';
import { Brain, CircleDot } from 'lucide-react';
import { describeError } from './lib/errors';
import { DEFAULT_MEMORY_GOVERNANCE, memoryGovernanceBody, memoryGovernanceDraft, type MemoryGovernance, type MemoryGovernanceDraft } from './lib/governance';
import * as gov from './lib/governanceApi';
import { formatDateTime, joinList, t } from './lib/i18n';
import { LoadingSkeleton, PaneProblem } from './PageParts';

type Act = (key: string, action: () => Promise<string>) => Promise<boolean>;

export function MemoryGovernanceCard({ canManage, busy, act }: { canManage: boolean; busy: string | null; act: Act }) {
  const [state, setState] = useState<{ kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; value: MemoryGovernance | null }>({ kind: 'loading' });
  const [draft, setDraft] = useState<MemoryGovernanceDraft>(memoryGovernanceDraft(DEFAULT_MEMORY_GOVERNANCE));
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const load = useCallback(async () => {
    try {
      const value = await gov.getMemoryGovernance();
      setState({ kind: 'ready', value });
      setDraft(memoryGovernanceDraft(value ?? DEFAULT_MEMORY_GOVERNANCE));
    } catch (e) { setState({ kind: 'error', message: describeError(e) }); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const save = async () => {
    const body = memoryGovernanceBody(draft);
    if (!body.ok) { setError(body.error); return; }
    setError(null);
    if (await act('memory-governance', async () => { await gov.saveMemoryGovernance(body.value); return t('gov.memory.saved'); })) await load();
  };
  const value = state.kind === 'ready' ? state.value : null;
  return <section className="surface govSection" aria-labelledby={`${id}-title`}>
    <div className="sectionHeader"><div><p className="eyebrow">{t('gov.memory.eyebrow')}</p><h2 id={`${id}-title`}>{t('gov.memory.title')}</h2><p className="muted">{t('gov.memory.intro')}</p></div></div>
    {state.kind === 'loading' && <LoadingSkeleton label={t('gov.loading')} rows={2} />}
    {state.kind === 'error' && <PaneProblem text={t('gov.memory.loadFailed', { message: state.message })} onRetry={() => void load()} />}
    {state.kind === 'ready' && <>
      {value ? <p className="muted memoryMeta"><Brain size={15} aria-hidden="true" />{t('gov.memory.updated', { user: value.updated_by ?? '-', when: value.updated_at ? formatDateTime(value.updated_at) : '-' })}</p>
        : <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('gov.memory.notConfigured')}</p>}
      {canManage ? <form className="govForm memoryForm" aria-label={t('gov.memory.title')} noValidate onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <div className="govGrid two">
          <label className="field" htmlFor={`${id}-days`}><span>{t('gov.memory.days')}</span>
            <input id={`${id}-days`} dir="ltr" inputMode="numeric" value={draft.retentionDays} aria-describedby={`${id}-days-hint`} onChange={(e) => setDraft({ ...draft, retentionDays: e.target.value })} />
            <small className="muted" id={`${id}-days-hint`}>{t('gov.memory.daysHint')}</small></label>
          <label className="field" htmlFor={`${id}-sources`}><span>{t('gov.memory.sources')}</span>
            <input id={`${id}-sources`} dir="ltr" value={draft.sourceTypes} placeholder="task, document, conversation" spellCheck={false} aria-describedby={`${id}-sources-hint`} onChange={(e) => setDraft({ ...draft, sourceTypes: e.target.value })} />
            <small className="muted" id={`${id}-sources-hint`}>{t('gov.memory.sourcesHint')}</small></label>
        </div>
        <label className="integrationToggle ruleCheck"><input type="checkbox" checked={draft.allowPermanent} onChange={(e) => setDraft({ ...draft, allowPermanent: e.target.checked })} /><span>{t('gov.memory.allowPermanent')}</span></label>
        <label className="integrationToggle ruleCheck"><input type="checkbox" checked={draft.requireProvenance} onChange={(e) => setDraft({ ...draft, requireProvenance: e.target.checked })} /><span>{t('gov.memory.requireProvenance')}</span></label>
        {error && <p className="notice errorNotice" role="alert">{error}</p>}
        <div className="actions"><button type="submit" disabled={busy !== null}>{t('gov.memory.save')}</button></div>
      </form>
      : <dl className="memoryFacts">
        <div><dt>{t('gov.memory.days')}</dt><dd dir="ltr">{(value ?? DEFAULT_MEMORY_GOVERNANCE).default_retention_days}</dd></div>
        <div><dt>{t('gov.memory.allowPermanent')}</dt><dd>{(value ?? DEFAULT_MEMORY_GOVERNANCE).allow_permanent_retention ? t('gov.yes') : t('gov.no')}</dd></div>
        <div><dt>{t('gov.memory.requireProvenance')}</dt><dd>{(value ?? DEFAULT_MEMORY_GOVERNANCE).require_provenance ? t('gov.yes') : t('gov.no')}</dd></div>
        <div><dt>{t('gov.memory.sources')}</dt><dd dir="ltr">{(value ?? DEFAULT_MEMORY_GOVERNANCE).allowed_source_types.length ? joinList((value ?? DEFAULT_MEMORY_GOVERNANCE).allowed_source_types) : t('gov.memory.anySource')}</dd></div>
      </dl>}
    </>}
  </section>;
}
