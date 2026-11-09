// Finance, Money advisor, Plan (docs/finance.md#planning-and-advice): which debt to pay first, can I afford this,
// cash or a loan or installments, and the cheapest way to send money home. Every figure is the API's, worked out
// from the person's own records; a live figure (a price, a rate, a fee) is shown with the address it was read from
// and the date. ANUM only advises: it never pays, buys or transfers. After a purchase, Purchases records it.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { Calculator, Globe, Landmark, Send, ShoppingBag, TriangleAlert } from 'lucide-react';
import type {
  FinanceAfford, FinanceCompare, FinanceOptionInput, FinancePayoffPage, FinanceRemittancePage, FinanceResearch, FinanceResearchTopic,
} from '@anum/contracts';
import * as pm from './lib/financePersonalApi';
import { formatAmount, isolate, type FinanceSection, type FinanceTab } from './lib/finance';
import { payoffHeadline, verdictTone } from './lib/financePersonal';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinCard, StatusChip } from './FinanceShell';
import './financePersonal.css';

const day = (value: string) => formatDateTime(`${value}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const amount = (value: string, currency: string) => isolate(formatAmount(value, currency));

export function AdvisorPlan({ base, onOpen }: { base: string; onOpen: (tab: FinanceTab, section?: FinanceSection) => void }) {
  return <div className="fpmPlan">
    <PayoffCard onOpen={onOpen} />
    <div className="finReportGrid fpmGrid">
      <AffordCard base={base} onOpen={onOpen} />
      <SendHomeCard onOpen={onOpen} />
    </div>
    <CompareCard base={base} />
  </div>;
}

// -- which debt first ------------------------------------------------------------------------------------

function PayoffCard({ onOpen }: { onOpen: (tab: FinanceTab) => void }) {
  const [payment, setPayment] = useState('');
  const [asked, setAsked] = useState<string | null>(null);
  const [state, setState] = useState<Load<FinancePayoffPage>>({ kind: 'loading' });
  const inputId = useId();
  const load = useCallback(() => {
    setState((s) => (s.kind === 'ready' ? s : { kind: 'loading' }));
    pm.getPayoff(asked).then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e)));
  }, [asked]);
  useEffect(() => { load(); }, [load]);
  return <FinCard title={t('fplanx.payoff.title')} icon={<Landmark />} subtitle={t('fplanx.payoff.sub')}>
    <form className="fpmInline" onSubmit={(e) => { e.preventDefault(); setAsked(payment.trim() || null); }}>
      <label className="field" htmlFor={inputId}><span>{t('fplanx.payoff.payment')}</span>
        <input id={inputId} value={payment} onChange={(e) => setPayment(e.target.value)} inputMode="decimal" dir="ltr" placeholder={t('fplanx.payoff.paymentHint')} /></label>
      <button type="submit">{t('fplanx.payoff.show')}</button>
    </form>
    {state.kind === 'loading' && <Notice busy>{t('fpm.loading')}</Notice>}
    {state.kind === 'denied' && <p className="muted">{t('finance.denied.text')}</p>}
    {state.kind === 'error' && <LoadError message={t('fpm.error', { message: state.message })} retry={load} />}
    {state.kind === 'ready' && (state.value.currencies.length === 0
      ? <div className="fpmEmptyLine"><p className="muted">{t('fplanx.payoff.none')}</p><button type="button" className="secondary" onClick={() => onOpen('debts')}>{t('fpm.fixed.addDebt')}</button></div>
      : state.value.currencies.map((p) => {
        const head = payoffHeadline(p);
        const c = p.currency;
        const when = (months: number | null) => (months === null ? t('fplanx.payoff.never') : t('fplanx.payoff.months', { count: months }));
        return <section key={c} className="fpmPayoff" aria-label={c}>
          <p className="fpmHeadline">{head.first ? t(p.cheaper === 'same' ? 'fplanx.payoff.same' : 'fplanx.payoff.first',
            { name: head.first, amount: amount(p.interest_saved, c), strategy: t(`fplanx.strategy.${head.strategy}` as MessageKey) }) : '-'}</p>
          <div className="finTableWrap" tabIndex={0}><table className="finGrid">
            <thead><tr><th scope="col">{t('fplanx.payoff.order')}</th><th scope="col">{t('fplanx.payoff.free')}</th><th scope="col" className="num">{t('fplanx.payoff.interest')}</th>
              <th scope="col">{t('fplanx.payoff.sequence')}</th></tr></thead>
            <tbody>{(['avalanche', 'snowball'] as const).map((s) => <tr key={s}>
              <th scope="row">{t(`fplanx.strategy.${s}` as MessageKey)}{p.cheaper === s ? <> <StatusChip tone="ok">{t('fplanx.payoff.cheaper')}</StatusChip></> : null}</th>
              <td>{when(p[s].months)}</td><td className="num"><Money value={p[s].total_interest} currency={c} /></td>
              <td dir="auto">{p[s].order.map((id) => p.debts.find((d) => d.id === id)?.name ?? '-').join(', ')}</td></tr>)}</tbody>
          </table></div>
          <p className="muted finFoot">{t('fplanx.payoff.paying', { amount: amount(p.payment, c) })}{p.below_minimums ? ` ${t('fplanx.payoff.below')}` : ''}</p>
          {p.missing_rates.length > 0 && <p className="finFoot finAdvisorBad"><TriangleAlert aria-hidden="true" />{t('fplanx.payoff.missing', { names: p.missing_rates.join(', ') })}</p>}
        </section>;
      }))}
  </FinCard>;
}

// -- can I afford this -------------------------------------------------------------------------------------

function AffordCard({ base, onOpen }: { base: string; onOpen: (tab: FinanceTab) => void }) {
  const [item, setItem] = useState(''), [price, setPrice] = useState(''), [currency, setCurrency] = useState(base), [monthly, setMonthly] = useState('');
  const [result, setResult] = useState<FinanceAfford | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null);
    try { setResult(await pm.afford({ price: price.trim(), currency, item: item.trim() || null, monthly_cost: monthly.trim() || null })); }
    catch (e) { setError(describeError(e)); setResult(null); } finally { setBusy(false); }
  };
  const c = result?.currency ?? currency;
  return <FinCard title={t('fplanx.afford.title')} icon={<ShoppingBag />} subtitle={t('fplanx.afford.sub')}>
    <form className="finForm" onSubmit={(e) => void submit(e)}>
      <div className="cmFormGrid">
        <label className="field"><span>{t('fplanx.afford.item')}</span><input value={item} onChange={(e) => setItem(e.target.value)} maxLength={120} dir="auto" /></label>
        <label className="field"><span>{t('fplanx.afford.price')}</span><input value={price} onChange={(e) => setPrice(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
        <label className="field"><span>{t('fpm.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
        <label className="field"><span>{t('fplanx.afford.monthly')}</span><input value={monthly} onChange={(e) => setMonthly(e.target.value)} inputMode="decimal" dir="ltr" /></label>
      </div>
      <div className="actions"><button type="submit" disabled={busy} aria-busy={busy || undefined}>{t('fplanx.afford.check')}</button></div>
    </form>
    {error && <Notice error>{error}</Notice>}
    {result && <div className="fpmResult" aria-live="polite">
      <p className={`fpmVerdict ${verdictTone(result.verdict)}`}>{t(`fplanx.verdict.${result.verdict}` as MessageKey)}</p>
      <dl className="finStatement">
        <div><dt>{t('fplanx.afford.safe')}</dt><dd>{result.safe_to_spend === null ? '-' : <Money value={result.safe_to_spend} currency={c} />}</dd></div>
        <div><dt>{t('fplanx.afford.liquid')}</dt><dd><Money value={result.liquid} currency={c} /></dd></div>
        <div><dt>{t('fplanx.afford.income')}</dt><dd>{result.income_monthly === null ? '-' : <Money value={result.income_monthly} currency={c} />}</dd></div>
        <div><dt>{t('fplanx.afford.commitments')}</dt><dd><Money value={result.commitments_monthly} currency={c} /></dd></div>
        {result.ratio_after !== null && <div><dt>{t('fplanx.afford.ratio')}</dt><dd dir="ltr">{Math.round(Number(result.ratio_after) * 100)}%</dd></div>}
        {result.months_left_after_cash !== null && result.monthly_cost === null && <div><dt>{t('fplanx.afford.reserve')}</dt><dd dir="ltr">{result.months_left_after_cash}</dd></div>}
      </dl>
      <p className="muted finFoot">{t('fplanx.ownData')}</p>
      {result.pay_with.length > 0 && <><h3 className="finAdvisorSub">{t('fplanx.afford.payWith')}</h3>
        <ul className="finMoveList">{result.pay_with.slice(0, 4).map((w) => <li key={w.account_id}><div><span dir="auto">{w.name}</span>
          <small>{t(`fplanx.pay.${w.note}` as MessageKey)}</small></div><Money value={w.balance} currency={w.currency} /></li>)}</ul></>}
      {result.avoid.length > 0 && <><h3 className="finAdvisorSub">{t('fplanx.afford.avoid')}</h3>
        <ul className="finRuleLines">{result.avoid.map((a, i) => <li key={`${a.code}-${i}`} className="near"><TriangleAlert aria-hidden="true" />
          <span>{t(`fplanx.avoid.${a.code}` as MessageKey, { ...a.params, currency: a.currency ?? '' })}</span></li>)}</ul></>}
      <div className="actions"><button type="button" className="secondary" onClick={() => { window.location.hash = '#purchases'; }}>{t('fplanx.afford.record')}</button>
        <button type="button" className="secondary" onClick={() => onOpen('safe')}>{t('fplanx.afford.openSafe')}</button></div>
    </div>}
    <LiveLookup topic="price" defaultQuery={item} label={t('fplanx.live.price')} />
  </FinCard>;
}

// -- cash, loan or installments ----------------------------------------------------------------------------

function CompareCard({ base }: { base: string }) {
  const [price, setPrice] = useState(''), [currency, setCurrency] = useState(base), [down, setDown] = useState(''), [months, setMonths] = useState('48');
  const [rate, setRate] = useState(''), [basis, setBasis] = useState<'reducing' | 'flat'>('reducing'), [source, setSource] = useState<{ url: string; on: string } | null>(null);
  const [instMonths, setInstMonths] = useState('4'), [instRate, setInstRate] = useState(''), [instFees, setInstFees] = useState('');
  const [result, setResult] = useState<FinanceCompare | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null);
    const options: FinanceOptionInput[] = [
      { kind: 'cash' },
      { kind: 'bank_loan', months: Number(months) || 12, rate_percent: rate.trim() || null, basis, down_payment: down.trim() || '0',
        rate_source: source?.url ?? null, rate_read_on: source?.on ?? null },
      { kind: 'installments', months: Number(instMonths) || 4, rate_percent: instRate.trim() || null, fees: instFees.trim() || '0', basis: 'flat' },
    ];
    try { setResult(await pm.compare({ price: price.trim(), currency, options })); } catch (e) { setError(describeError(e)); setResult(null); } finally { setBusy(false); }
  };
  return <FinCard title={t('fplanx.compare.title')} icon={<Calculator />} subtitle={t('fplanx.compare.sub')}>
    <form className="finForm" onSubmit={(e) => void submit(e)}>
      <div className="cmFormGrid fpmCompareGrid">
        <label className="field"><span>{t('fplanx.afford.price')}</span><input value={price} onChange={(e) => setPrice(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
        <label className="field"><span>{t('fpm.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
        <label className="field"><span>{t('fplanx.compare.down')}</span><input value={down} onChange={(e) => setDown(e.target.value)} inputMode="decimal" dir="ltr" /></label>
        <label className="field"><span>{t('fplanx.compare.months')}</span><input type="number" min={1} max={360} value={months} onChange={(e) => setMonths(e.target.value)} /></label>
        <label className="field"><span>{t('fplanx.compare.rate')}</span><input value={rate} onChange={(e) => { setRate(e.target.value); setSource(null); }} inputMode="decimal" dir="ltr" /></label>
        <label className="field"><span>{t('fplanx.compare.basis')}</span><select value={basis} onChange={(e) => setBasis(e.target.value as 'reducing' | 'flat')}>
          <option value="reducing">{t('fplanx.compare.reducing')}</option><option value="flat">{t('fplanx.compare.flat')}</option></select></label>
        <label className="field"><span>{t('fplanx.compare.instMonths')}</span><input type="number" min={1} max={60} value={instMonths} onChange={(e) => setInstMonths(e.target.value)} /></label>
        <label className="field"><span>{t('fplanx.compare.instRate')}</span><input value={instRate} onChange={(e) => setInstRate(e.target.value)} inputMode="decimal" dir="ltr" /></label>
        <label className="field"><span>{t('fplanx.compare.instFees')}</span><input value={instFees} onChange={(e) => setInstFees(e.target.value)} inputMode="decimal" dir="ltr" /></label>
      </div>
      {source && <p className="muted finFoot">{t('fplanx.compare.usingSource', { url: source.url, date: day(source.on) })}</p>}
      <div className="actions"><button type="submit" disabled={busy} aria-busy={busy || undefined}>{t('fplanx.compare.run')}</button></div>
    </form>
    {error && <Notice error>{error}</Notice>}
    {result && <div className="fpmResult" aria-live="polite"><div className="finTableWrap" tabIndex={0}><table className="finGrid">
      <thead><tr><th scope="col">{t('fplanx.compare.option')}</th><th scope="col" className="num">{t('fplanx.compare.monthly')}</th><th scope="col" className="num">{t('fplanx.compare.total')}</th>
        <th scope="col" className="num">{t('fplanx.compare.interest')}</th><th scope="col">{t('fplanx.compare.rateFrom')}</th></tr></thead>
      <tbody>{result.options.map((o, i) => <tr key={`${o.kind}-${i}`}>
        <th scope="row">{t(`fplanx.option.${o.kind}` as MessageKey)}</th>
        <td className="num">{o.monthly ? <Money value={o.monthly} currency={result.currency} /> : '-'}{o.monthly ? <small> {t('fplanx.compare.forMonths', { count: o.months })}</small> : null}</td>
        <td className="num">{o.total_cost ? <Money value={o.total_cost} currency={result.currency} /> : t('fplanx.compare.unknown')}</td>
        <td className="num">{o.interest ? <Money value={o.interest} currency={result.currency} /> : '-'}</td>
        <td>{o.kind === 'cash' ? '-' : o.status === 'rate_unknown' ? t('fplanx.compare.rateUnknown')
          : o.rate_source === 'owner' ? t('fplanx.compare.yourRate', { rate: isolate(o.rate_percent ?? '0') })
            : <a href={o.rate_source} target="_blank" rel="noreferrer noopener" dir="ltr">{t('fplanx.compare.liveRate', { rate: o.rate_percent ?? '-', date: o.rate_read_on ? day(o.rate_read_on) : '-' })}</a>}</td>
      </tr>)}</tbody>
    </table></div><p className="muted finFoot">{t('fplanx.compare.estimate')}</p></div>}
    <LiveLookup topic="loan_rate" defaultQuery="car" label={t('fplanx.live.rate')} onUse={(value, url, on) => { setRate(value); setSource({ url, on }); }} />
  </FinCard>;
}

// -- sending money home ------------------------------------------------------------------------------------

function SendHomeCard({ onOpen }: { onOpen: (tab: FinanceTab, section?: FinanceSection) => void }) {
  const [state, setState] = useState<Load<FinanceRemittancePage>>({ kind: 'loading' });
  const load = useCallback(() => { pm.listRemittances({ limit: 1, offset: 0 }).then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e))); }, []);
  useEffect(() => { load(); }, [load]);
  const providers = state.kind === 'ready' ? state.value.providers : [];
  return <FinCard title={t('fplanx.send.title')} icon={<Send />} subtitle={t('fplanx.send.sub')}>
    {state.kind === 'loading' && <Notice busy>{t('fpm.loading')}</Notice>}
    {state.kind === 'error' && <LoadError message={t('fpm.error', { message: state.message })} retry={load} />}
    {state.kind === 'ready' && (providers.length === 0
      ? <div className="fpmEmptyLine"><p className="muted">{t('fplanx.send.none')}</p><button type="button" className="secondary" onClick={() => onOpen('goals', 'send-home')}>{t('fpm.remit.add')}</button></div>
      : <ul className="finMoveList">{providers.slice(0, 5).map((p, i) => <li key={`${p.provider}-${p.corridor}`}>
        <div><span dir="auto">{p.provider}</span><small dir="ltr">{p.corridor} · {p.latest_on}</small></div>
        <span>{p.average_fee_percent !== null ? t('fpm.remit.feePct', { percent: isolate(p.average_fee_percent) }) : '-'}</span>
        <span dir="ltr">{p.latest_effective_rate ?? '-'}</span>
        {i === 0 && <StatusChip tone="ok">{t('fpm.remit.cheapest')}</StatusChip>}</li>)}</ul>)}
    <p className="muted finFoot">{t('fplanx.send.howRead')}</p>
    <LiveLookup topic="remittance" defaultQuery="AED to INR" label={t('fplanx.live.remit')} />
  </FinCard>;
}

// -- live figures ------------------------------------------------------------------------------------------

function LiveLookup({ topic, defaultQuery, label, onUse }: {
  topic: FinanceResearchTopic; defaultQuery: string; label: string; onUse?: (value: string, url: string, readOn: string) => void;
}) {
  const [query, setQuery] = useState(defaultQuery), [country, setCountry] = useState('UAE');
  const [result, setResult] = useState<FinanceResearch | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const queryId = useId(), countryId = useId();
  const run = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null);
    try { setResult(await pm.research({ topic, query: query.trim() || defaultQuery || topic, country: country.trim() || null })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <details className="fpmLive">
    <summary><Globe aria-hidden="true" />{label}</summary>
    <form className="fpmInline" onSubmit={(e) => void run(e)}>
      <label className="field" htmlFor={queryId}><span>{t('fplanx.live.query')}</span><input id={queryId} value={query} onChange={(e) => setQuery(e.target.value)} maxLength={200} dir="auto" /></label>
      <label className="field" htmlFor={countryId}><span>{t('fplanx.live.country')}</span><input id={countryId} value={country} onChange={(e) => setCountry(e.target.value)} maxLength={60} dir="auto" /></label>
      <button type="submit" className="secondary" disabled={busy} aria-busy={busy || undefined}>{t('fplanx.live.look')}</button>
    </form>
    {error && <Notice error>{error}</Notice>}
    {result && (result.status !== 'ok'
      ? <p className="muted" role="status">{result.status === 'unavailable' ? t('fplanx.live.unavailable') : t('fplanx.live.unknown')}</p>
      : <><ul className="fpmLiveList">{result.items.map((item) => <li key={item.url}>
        <a href={item.url} target="_blank" rel="noreferrer noopener" dir="auto">{item.title}</a>
        <small dir="ltr">{item.url} · {t('fplanx.live.read', { date: day(item.read_on) })}</small>
        {item.figures.length === 0 ? <small className="muted">{t('fplanx.live.noFigure')}</small> : <span className="fpmFigures">{item.figures.map((f) => {
          const text = f.kind === 'percent' ? `${f.value}%` : `${f.currency ?? ''} ${f.value}`.trim();
          return onUse && f.kind === 'percent'
            ? <button key={text} type="button" className="secondary" onClick={() => onUse(f.value, item.url, item.read_on)} aria-label={t('fplanx.live.useAria', { value: text })}>{t('fplanx.live.use', { value: text })}</button>
            : <StatusChip key={text} tone="info">{text}</StatusChip>;
        })}</span>}
      </li>)}</ul><p className="muted finFoot">{t('fplanx.live.note')}</p></>)}
  </details>;
}
