// Live eligibility check (docs/finance.md#live-eligibility-preflight): the person ticks what is true for
// their broker and the server answers what is still missing before live could ever be considered, with
// its own live preconditions. Read only: it connects nothing, reads no key, sends no order and saves only
// an audit row with ids. Whatever is ticked, the answer says live trading is off and zero orders were sent.
import { useId, useRef, useState, type FormEvent } from 'react';
import { Ban, CircleCheck, CircleX, ClipboardCheck, ExternalLink, Info } from 'lucide-react';
import type { FinanceBrokerAsset, FinanceBrokerKind, FinancePreflight, FinancePreflightFact } from '@anum/contracts';
import * as api from './lib/financeApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import {
  ASSETS, BROKERS, PREFLIGHT_GROUP_NAMES, PREFLIGHT_GROUPS, emptyPreflightDraft, missingCount, missingIn, preflightRequest, safeSource,
  type PreflightDraft,
} from './lib/financePreflight';
import { FormError, LoadError, Notice } from './FinanceParts';

type State = { kind: 'idle' } | { kind: 'loading' } | { kind: 'ready'; value: FinancePreflight } | { kind: 'denied' } | { kind: 'error'; message: string };

const factLabel = (fact: FinancePreflightFact) => t(`finance.preflight.fact.${fact}` as MessageKey);

export function LivePreflight() {
  const id = useId();
  const [draft, setDraft] = useState<PreflightDraft>(emptyPreflightDraft);
  const [state, setState] = useState<State>({ kind: 'idle' });
  const [formError, setFormError] = useState<string | null>(null);
  const run = useRef(0);
  const set = <K extends keyof PreflightDraft>(key: K, value: PreflightDraft[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setFormError(null);
  };

  async function check(event?: FormEvent) {
    event?.preventDefault();
    const request = preflightRequest(draft);
    if (!request.ok) { setFormError(request.error); return; }
    const mine = ++run.current;
    setFormError(null);
    setState({ kind: 'loading' });
    try {
      const value = await api.livePreflight(request.value);
      if (mine === run.current) setState({ kind: 'ready', value });
    } catch (e) {
      if (mine === run.current) setState(isPermissionDenied(e) ? { kind: 'denied' } : { kind: 'error', message: describeError(e) });
    }
  }

  function clear() {
    run.current++;
    setDraft(emptyPreflightDraft());
    setFormError(null);
    setState({ kind: 'idle' });
  }

  const busy = state.kind === 'loading';
  const toggleAsset = (asset: FinanceBrokerAsset, on: boolean) =>
    set('assets', on ? [...draft.assets.filter((a) => a !== asset), asset] : draft.assets.filter((a) => a !== asset));

  return <section className="surface cmPanel finPreflight" aria-labelledby={`${id}-title`}>
    <div className="sectionHeader">
      <h2 id={`${id}-title`}><ClipboardCheck aria-hidden="true" className="cmInlineIcon" />{t('finance.preflight.title')}</h2>
      <span className="statusBadge neutral">{t('finance.preflight.readOnly')}</span>
    </div>
    <p className="muted">{t('finance.preflight.intro')}</p>
    <form className="finForm" onSubmit={(e) => void check(e)} aria-describedby={formError ? `${id}-error` : undefined}>
      <div className="finPreflightProfile">
        <div className="field"><label htmlFor={`${id}-broker`}>{t('finance.preflight.broker')}</label>
          <select id={`${id}-broker`} value={draft.broker} onChange={(e) => set('broker', e.target.value as FinanceBrokerKind | '')}>
            <option value="">{t('finance.preflight.broker.none')}</option>
            {BROKERS.map((b) => <option key={b} value={b}>{t(`finance.preflight.broker.${b}` as MessageKey)}</option>)}
          </select></div>
        {draft.broker && <div className="field"><label htmlFor={`${id}-country`}>{t('finance.preflight.country')}</label>
          <input id={`${id}-country`} value={draft.country} maxLength={2} dir="ltr" autoComplete="country" className="finCurrencyInput"
            onChange={(e) => set('country', e.target.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 2))} /></div>}
      </div>
      {draft.broker && <fieldset className="finMapping"><legend>{t('finance.preflight.assets')}</legend>
        <div className="finChecks">{ASSETS.map((asset) => <label key={asset} className="finCheck">
          <input type="checkbox" checked={draft.assets.includes(asset)} onChange={(e) => toggleAsset(asset, e.target.checked)} />
          <span>{t(`finance.preflight.asset.${asset}` as MessageKey)}</span></label>)}</div>
      </fieldset>}
      {PREFLIGHT_GROUP_NAMES.map((group) => <fieldset key={group} className="finMapping"><legend>{t(`finance.preflight.group.${group}` as MessageKey)}</legend>
        <div className="finPreflightFacts">{PREFLIGHT_GROUPS[group].map((fact) => <label key={fact} className="finCheck">
          <input type="checkbox" checked={Boolean(draft.facts[fact])} onChange={(e) => set('facts', { ...draft.facts, [fact]: e.target.checked })} />
          <span>{factLabel(fact)}</span></label>)}</div>
      </fieldset>)}
      <p className="muted finHelp">{t('finance.preflight.notEvidence')}</p>
      <FormError id={`${id}-error`} text={formError} />
      <div className="actions">
        <button type="submit" disabled={busy}>{busy ? t('finance.preflight.checking') : t('finance.preflight.check')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={clear}>{t('finance.preflight.reset')}</button>
      </div>
    </form>
    <div className="finPreflightResult" aria-live="polite">
      {state.kind === 'idle' && <p className="muted">{t('finance.preflight.idle')}</p>}
      {state.kind === 'loading' && <Notice busy>{t('finance.preflight.checking')}</Notice>}
      {state.kind === 'denied' && <Notice error>{t('finance.denied.text')}</Notice>}
      {state.kind === 'error' && <LoadError message={t('finance.preflight.error', { message: state.message })} retry={() => void check()} />}
      {state.kind === 'ready' && <PreflightResult result={state.value} titleId={`${id}-result`} />}
    </div>
  </section>;
}

function PreflightResult({ result, titleId }: { result: FinancePreflight; titleId: string }) {
  const missing = missingCount(result);
  return <section aria-labelledby={titleId} className="finPreflightAnswer">
    <h3 id={titleId} className="finSubHead">{t('finance.preflight.result')}</h3>
    <p className="notice errorNotice finLiveOff" role="status"><Ban aria-hidden="true" />
      <span><strong>{t('finance.preflight.liveOff')}</strong> {t('finance.preflight.ordersSent', { count: result.orders_sent })}. {t('finance.preflight.stage', { stage: t(`finance.preflight.stage.${result.stage}` as MessageKey) })}.</span></p>
    <p>{missing ? t('finance.preflight.missing', { count: missing }) : t('finance.preflight.allTicked')}</p>
    <div className="finPreflightGroups">
      {PREFLIGHT_GROUP_NAMES.map((group) => {
        const items = missingIn(result, group);
        return <div key={group}>
          <h4 className="finSubHead">{t(`finance.preflight.group.${group}` as MessageKey)}</h4>
          {items.length ? <ul className="finRules finPreconditions">{items.map((fact) => <li key={fact} className="finUnmet">
            <CircleX aria-hidden="true" /><span>{factLabel(fact)}<span className="visuallyHidden"> {t('finance.live.unmet')}</span></span></li>)}</ul>
            : <p className="muted">{t('finance.preflight.noneMissing')}</p>}
        </div>;
      })}
    </div>
    <h4 className="finSubHead">{t('finance.preflight.system')}</h4>
    <ul className="finRules finPreconditions">{result.system_preconditions.map((p) => <li key={p.code} className={p.met ? '' : 'finUnmet'}>
      {p.met ? <CircleCheck aria-hidden="true" /> : <CircleX aria-hidden="true" />}
      <span>{t(`finance.live.pre.${p.code}` as MessageKey)}<span className="visuallyHidden"> {t(p.met ? 'finance.live.met' : 'finance.live.unmet')}</span></span></li>)}</ul>
    {(result.broker_gates ?? []).length > 0 && <>
      <h4 className="finSubHead">{t('finance.preflight.brokerGates')}</h4>
      <ul className="finRules finPreconditions">{(result.broker_gates ?? []).map((p) => <li key={p.code} className={p.met ? '' : 'finUnmet'}>
        {p.met ? <CircleCheck aria-hidden="true" /> : <CircleX aria-hidden="true" />}
        <span>{p.message}<span className="visuallyHidden"> {t(p.met ? 'finance.live.met' : 'finance.live.unmet')}</span></span></li>)}</ul></>}
    {result.notes.length > 0 && <>
      <h4 className="finSubHead">{t('finance.preflight.notes')}</h4>
      <ul className="finRules">{result.notes.map((note) => {
        const href = safeSource(note.source);
        return <li key={note.code}><Info aria-hidden="true" /><span>{t(`finance.preflight.note.${note.code}` as MessageKey)}
          {href && <> <a href={href} target="_blank" rel="noopener noreferrer">{t('finance.preflight.source')}<ExternalLink aria-hidden="true" className="cmInlineIcon" /></a></>}</span></li>;
      })}</ul>
    </>}
  </section>;
}
