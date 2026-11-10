// Foreign balances in the base currency (docs/finance.md#foreign-currency-balances): each account in another currency, worth
// now at the newest stored rate and worth when the money came in, with the unrealised gain or loss. Display only: the API
// never posts it or counts it as spending or income, and a missing rate is said, never guessed. On the Finance overview and
// the Accounts page (one account when one is chosen).
import { useCallback, useEffect, useState } from 'react';
import { Globe2 } from 'lucide-react';
import type { FinanceFxRevaluation } from '@anum/contracts';
import * as pm from './lib/financePersonalApi';
import { formatAmount, isolate, type FinanceTab } from './lib/finance';
import { revaluationRows, revaluationTone } from './lib/financeDepth2';
import { formatDateTime, t } from './lib/i18n';
import { Denied, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinCard, StatusChip } from './FinanceShell';
import './financeDepth2.css';

const day = (value: string) => formatDateTime(`${value}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const amount = (value: string, currency: string) => isolate(formatAmount(value, currency));

export function FxBalancesCard({ accountId = null, onOpen, className = '' }: { accountId?: string | null; onOpen?: (tab: FinanceTab) => void; className?: string }) {
  const [state, setState] = useState<Load<FinanceFxRevaluation>>({ kind: 'loading' });
  const load = useCallback(() => {
    setState((c) => (c.kind === 'ready' ? c : { kind: 'loading' }));
    pm.getFxRevaluation(accountId).then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e)));
  }, [accountId]);
  useEffect(() => { load(); }, [load]);
  if (state.kind === 'ready' && revaluationRows(state.value.items).length === 0 && accountId) return null;  // the chosen account is in the base currency
  const base = state.kind === 'ready' ? state.value.base_currency : '';
  return <FinCard title={t('fdepth2.fx.title', { base: base || '-' })} icon={<Globe2 />} className={`fdepth2Fx ${className}`}
    subtitle={state.kind === 'ready' ? t('fdepth2.fx.subtitle', { base }) : undefined}
    actions={onOpen ? <button type="button" className="cmLinkButton" onClick={() => onOpen('connections')}>{t('fdepth2.fx.rates')}</button> : undefined}>
    {state.kind === 'loading' && <Notice busy>{t('finance.loading')}</Notice>}
    {state.kind === 'denied' && <Denied />}
    {state.kind === 'error' && <LoadError message={t('fdepth2.fx.error', { message: state.message })} retry={load} />}
    {state.kind === 'ready' && <FxBody page={state.value} />}
  </FinCard>;
}

function FxBody({ page }: { page: FinanceFxRevaluation }) {
  const rows = revaluationRows(page.items);
  if (rows.length === 0) return <p className="muted">{t('fdepth2.fx.empty', { base: page.base_currency })}</p>;
  return <>
    <ul className="fdepth2FxList">{rows.map((r) => <li key={r.account_id}>
      <div className="fdepth2FxHead"><strong dir="auto">{r.name}</strong><Money value={r.balance} currency={r.currency} /></div>
      {r.status === 'rate_not_available'
        ? <p className="fdepth2FxMissing"><StatusChip tone="warn">{t('fdepth2.fx.noRate')}</StatusChip>
          <small className="muted">{t('fdepth2.fx.noRateHelp', { currency: r.currency, base: r.base_currency, days: page.rate_max_age_days })}</small></p>
        : <dl className="fdepth2FxFigures">
          <div><dt>{t('fdepth2.fx.value')}</dt><dd>{r.value ? <Money value={r.value} currency={r.base_currency} /> : '-'}</dd></div>
          <div><dt>{t('fdepth2.fx.cost')}</dt><dd>{r.cost ? <Money value={r.cost} currency={r.base_currency} /> : <StatusChip tone="warn">{t('fdepth2.fx.noRate')}</StatusChip>}</dd></div>
          <div><dt>{t('fdepth2.fx.change')}</dt><dd>{r.unrealised !== null
            ? <StatusChip tone={revaluationTone(r)}><bdi dir="ltr">{formatAmount(r.unrealised, r.base_currency)}</bdi></StatusChip> : '-'}</dd></div>
        </dl>}
      {r.rate && r.rate_as_of && <small className="muted fdepth2FxRate">{t('fdepth2.fx.rate', { currency: r.currency, rate: isolate(r.rate), base: r.base_currency,
        date: day(r.rate_as_of), source: r.rate_source ?? '-' })}</small>}
      {r.status === 'cost_rate_not_available' && <small className="muted fdepth2FxRate">
        {t('fdepth2.fx.noCost', { dates: r.missing_rate_dates.map(day).join(', ') })}. {t('fdepth2.fx.noCostHelp')}</small>}
    </li>)}</ul>
    {page.unrealised_total !== null && <p className="fdepth2FxTotal">{t('fdepth2.fx.total', { amount: amount(page.unrealised_total, page.base_currency) })}</p>}
    {page.incomplete > 0 && <p className="muted">{t('fdepth2.fx.incomplete', { count: page.incomplete })}</p>}
  </>;
}
