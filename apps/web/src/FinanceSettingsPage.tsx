// Finance settings (docs/finance.md#finance-settings), `#finance-settings`: everything that shapes the Finance
// figures in one page. Every control saves through the existing endpoints (PUT /finance/settings, PUT /finance/plan
// through the Safe to spend dialog, POST or PATCH /finance/goals for the emergency reserve); the rest are links to
// the pages that own them (salary profile, rules, automation, import, privacy, connection health). Nothing here
// moves money.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import {
  Activity, BadgeDollarSign, CalendarClock, FileUp, Globe2, LifeBuoy, ListChecks, Plus, ShieldCheck, Settings2, SlidersHorizontal, Trash2, Wand2,
} from 'lucide-react';
import type {
  FinanceAccount, FinanceCategory, FinanceGoal, FinanceGoalInput, FinancePlan, FinanceReserves, FinanceSalaryProfile, FinanceSettings,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import * as personal from './lib/financePersonalApi';
import { describeError } from './lib/errors';
import { categoryLabel, formatAmount, isolate, parseAmount, type FinanceTab } from './lib/finance';
import { monthsTarget, reserveFor, targetDrifted } from './lib/financeForecast';
import { formatDateTime, t } from './lib/i18n';
import { Denied, FormError, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinCard, FinancePageHead } from './FinanceShell';
import { PlanDialog } from './FinancePlan';
import { ReserveCard } from './FinanceForecast';
import './financePages.css';

const CATEGORIES: FinanceCategory[] = [
  'groceries', 'dining', 'food', 'room_rent', 'transport', 'fuel', 'utilities', 'telecom', 'housing', 'shopping', 'health', 'education', 'entertainment',
  'subscriptions', 'travel', 'insurance', 'fees', 'interest', 'cash', 'transfer', 'income', 'cashback', 'refunds', 'other',
];
const ZONES = ['Asia/Dubai', 'Asia/Riyadh', 'Asia/Qatar', 'Asia/Kuwait', 'Asia/Bahrain', 'Asia/Muscat', 'Asia/Karachi', 'Asia/Kolkata', 'Europe/London', 'UTC'];

function useLoad<T>(load: () => Promise<T>): [Load<T>, () => void] {
  const [state, setState] = useState<Load<T>>({ kind: 'loading' });
  const run = useCallback(() => {
    setState((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    load().then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e)));
  }, [load]);
  useEffect(() => { run(); }, [run]);
  return [state, run];
}

export function FinanceSettingsPage({ onOpen }: { onOpen: (tab: FinanceTab) => void }) {
  const [settings, reloadSettings] = useLoad(api.getSettings);
  const [plan, reloadPlan] = useLoad(api.getPlan);
  const [reserves, reloadReserves] = useLoad(api.getReserve);
  const [goals, reloadGoals] = useLoad(api.listGoals);
  const [accounts] = useLoad(api.listAccounts);
  const [salary] = useLoad(personal.getSalaryProfile);
  const [planOpen, setPlanOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const head = <FinancePageHead title={t('fset.title')} subtitle={t('fset.subtitle')} icon={<Settings2 />} />;
  if (settings.kind === 'denied') return <div className="finPage finSettingsPage">{head}<Denied /></div>;
  const base = settings.kind === 'ready' ? settings.value.base_currency : 'AED';
  const reserveChanged = () => { reloadReserves(); reloadGoals(); setNotice(t('fset.reserve.savedNotice')); };
  return <div className="finPage finSettingsPage">
    {head}
    {notice && <Notice>{notice}</Notice>}
    <div className="finSettingsGrid">
      <GeneralCard settings={settings} retry={reloadSettings} onSaved={() => { reloadSettings(); reloadReserves(); setNotice(t('fset.saved')); }} />
      <FinCard title={t('fset.safe.title')} subtitle={t('fset.safe.subtitle')} icon={<SlidersHorizontal />} className="finSettingsSafe"
        actions={<button type="button" className="secondary" onClick={() => setPlanOpen(true)}>{t('fset.edit')}</button>}>
        <PlanSummary plan={plan} retry={reloadPlan} />
      </FinCard>
      <FinCard title={t('fset.reserve.title')} subtitle={t('fset.reserve.subtitle')} icon={<LifeBuoy />} className="finSettingsReserve">
        <ReserveForm reserves={reserves} goals={goals} accounts={accounts} base={base} retry={() => { reloadReserves(); reloadGoals(); }} onSaved={reserveChanged} />
      </FinCard>
      <SalaryCard salary={salary} onOpen={() => { location.hash = '#finance-advisor-salary'; }} />
      <FinCard title={t('fset.rules.title')} subtitle={t('fset.rules.subtitle')} icon={<Wand2 />}>
        <ul className="finSettingsLinks">
          <li><a href="#finance-suggestions?view=rules"><ListChecks aria-hidden="true" />{t('fset.rules.rules')}</a><small className="muted">{t('fset.rules.rulesText')}</small></li>
          <li><a href="#finance-suggestions?view=merchants"><BadgeDollarSign aria-hidden="true" />{t('fset.rules.merchants')}</a><small className="muted">{t('fset.rules.merchantsText')}</small></li>
          <li><a href="#finance-suggestions?view=automation"><Activity aria-hidden="true" />{t('fset.rules.automation')}</a><small className="muted">{t('fset.rules.automationText')}</small></li>
        </ul>
      </FinCard>
      <FinCard title={t('fset.data.title')} subtitle={t('fset.data.subtitle')} icon={<FileUp />}>
        <ul className="finSettingsLinks">
          <li><a href="#finance-import" onClick={(e) => { e.preventDefault(); onOpen('import'); }}><FileUp aria-hidden="true" />{t('fset.data.import')}</a><small className="muted">{t('fset.data.importText')}</small></li>
          <li><a href="#finance-reconciliation" onClick={(e) => { e.preventDefault(); onOpen('reconciliation'); }}><Activity aria-hidden="true" />{t('fset.data.health')}</a><small className="muted">{t('fset.data.healthText')}</small></li>
          <li><a href="#finance-connections" onClick={(e) => { e.preventDefault(); onOpen('connections'); }}><Globe2 aria-hidden="true" />{t('fset.data.connections')}</a><small className="muted">{t('fset.data.connectionsText')}</small></li>
        </ul>
      </FinCard>
      <FinCard title={t('fset.privacy.title')} subtitle={t('fset.privacy.subtitle')} icon={<ShieldCheck />}>
        <ul className="finSettingsLinks">
          <li><a href="#privacy">{t('fset.privacy.export')}</a><small className="muted">{t('fset.privacy.exportText')}</small></li>
          <li><a href="#privacy">{t('fset.privacy.delete')}</a><small className="muted">{t('fset.privacy.deleteText')}</small></li>
        </ul>
      </FinCard>
    </div>
    {planOpen && <PlanDialog onClose={(saved) => { setPlanOpen(false); if (saved) { reloadPlan(); setNotice(t('finance.safe.saved')); } }} />}
  </div>;
}

/** Main currency, Finance time zone and the statement keyword rules (formerly the Settings dialog): PUT /finance/settings. */
function GeneralCard({ settings, retry, onSaved }: { settings: Load<FinanceSettings>; retry: () => void; onSaved: () => void }) {
  const [currency, setCurrency] = useState('AED'), [zone, setZone] = useState('Asia/Dubai');
  const [rules, setRules] = useState<{ match: string; category: FinanceCategory }[]>([]);
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId(), zones = useId();
  const ready = settings.kind === 'ready' ? settings.value : null;
  useEffect(() => { if (ready) { setCurrency(ready.base_currency); setZone(ready.timezone); setRules(ready.category_rules); } }, [ready]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!/^[A-Za-z]{3}$/.test(currency)) { setError(t('finance.error.currency')); return; }
    const kept = rules.filter((r) => r.match.trim());
    if (kept.some((r) => r.match.trim().length < 2)) { setError(t('finance.error.rule')); return; }
    setBusy(true); setError(null);
    try { await api.saveSettings({ base_currency: currency.toUpperCase(), timezone: zone.trim(), category_rules: kept.map((r) => ({ match: r.match.trim(), category: r.category })) }); onSaved(); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <FinCard title={t('fset.general.title')} subtitle={t('fset.general.subtitle')} icon={<Globe2 />} className="finSettingsGeneral">
    {settings.kind === 'loading' ? <Notice busy>{t('finance.loading')}</Notice>
      : settings.kind !== 'ready' ? <LoadError message={t('fset.error', { message: settings.message })} retry={retry} />
      : <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
        <div className="cmFormGrid">
          <label className="field"><span>{t('finance.settings.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
          <label className="field"><span>{t('finance.settings.timezone')}</span><input value={zone} onChange={(e) => setZone(e.target.value)} maxLength={64} dir="ltr" list={zones} required /></label>
          <datalist id={zones}>{ZONES.map((z) => <option key={z} value={z} />)}</datalist>
        </div>
        <p className="muted finHelp">{t('fset.general.zoneHelp')}</p>
        <fieldset className="finMapping"><legend>{t('fset.import.rules')}</legend>
          <p className="muted">{t('fset.import.rulesText')}</p>
          {rules.length === 0 && <p className="muted">{t('fset.import.none')}</p>}
          {rules.map((rule, index) => <div key={index} className="cmFilters">
            <label className="cmInline cmSearch"><span>{t('finance.settings.match')}</span><input value={rule.match} onChange={(e) => setRules(rules.map((r, i) => i === index ? { ...r, match: e.target.value } : r))} maxLength={80} dir="auto" /></label>
            <label className="cmInline"><span>{t('finance.col.category')}</span><select value={rule.category} onChange={(e) => setRules(rules.map((r, i) => i === index ? { ...r, category: e.target.value as FinanceCategory } : r))}>{CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
            <button type="button" className="iconButton" onClick={() => setRules(rules.filter((_, i) => i !== index))} aria-label={t('finance.settings.removeRule', { match: rule.match || String(index + 1) })}><Trash2 aria-hidden="true" /></button>
          </div>)}
          <button type="button" className="secondary" disabled={rules.length >= 100} onClick={() => setRules([...rules, { match: '', category: 'other' }])}><Plus aria-hidden="true" />{t('finance.settings.addRule')}</button>
        </fieldset>
        <FormError id={errorId} text={error} />
        <div className="actions"><button type="submit" disabled={busy}>{busy ? t('fset.saving') : t('fset.save')}</button></div>
      </form>}
  </FinCard>;
}

function PlanSummary({ plan, retry }: { plan: Load<FinancePlan>; retry: () => void }) {
  if (plan.kind === 'loading') return <Notice busy>{t('finance.loading')}</Notice>;
  if (plan.kind !== 'ready') return <LoadError message={t('fset.error', { message: plan.message })} retry={retry} />;
  const p = plan.value;
  return <ul className="finRuleLines">
    {p.buffers.length === 0 ? <li>{t('fset.safe.noBuffer')}</li>
      : p.buffers.map((b) => <li key={b.currency}>{t('fset.safe.buffer', { amount: isolate(formatAmount(b.amount, b.currency)) })}</li>)}
    <li>{p.combine_currencies ? t('fset.safe.combineOn', { days: p.fx_max_age_days }) : t('fset.safe.combineOff')}</li>
  </ul>;
}

function SalaryCard({ salary, onOpen }: { salary: Load<{ profile: FinanceSalaryProfile | null }>; onOpen: () => void }) {
  const profile = salary.kind === 'ready' ? salary.value.profile : null;
  return <FinCard title={t('fset.salary.title')} subtitle={t('fset.salary.subtitle')} icon={<CalendarClock />}
    actions={<button type="button" className="secondary" onClick={onOpen}>{profile ? t('fset.salary.open') : t('fset.salary.add')}</button>}>
    {salary.kind === 'loading' ? <Notice busy>{t('finance.loading')}</Notice>
      : salary.kind !== 'ready' ? <p className="muted">{t('fset.error', { message: salary.message })}</p>
      : !profile ? <p className="muted">{t('fset.salary.none')}</p>
      : <ul className="finRuleLines">
        <li><span dir="auto">{profile.employer}</span></li>
        <li>{t('fset.salary.net', { amount: isolate(formatAmount(profile.expected_net, profile.currency)) })}</li>
        <li>{t('fset.salary.next', { date: isolate(formatDateTime(`${profile.next_pay_on}T12:00:00Z`, { day: 'numeric', month: 'short', timeZone: 'UTC' })) })}</li>
      </ul>}
  </FinCard>;
}

type Holder = 'goal' | string;

/**
 * The emergency reserve: one goal with the purpose "emergency" per currency, sized in months of essential spending
 * (the target is months x the essential monthly figure GET /reserve gives) or as a fixed amount, kept in an account or
 * in the goal itself. Saved with POST or PATCH /finance/goals, so Safe to spend and the forecast read the same target.
 */
function ReserveForm({ reserves, goals, accounts, base, retry, onSaved }: {
  reserves: Load<FinanceReserves>; goals: Load<FinanceGoal[]>; accounts: Load<FinanceAccount[]>; base: string; retry: () => void; onSaved: () => void;
}) {
  const ready = reserves.kind === 'ready' ? reserves.value : null;
  const [currency, setCurrency] = useState<string | null>(null);
  const chosen = currency ?? ready?.reserves[0]?.currency ?? base;
  const { reserve, suggestion } = reserveFor(ready, chosen);
  const goal = goals.kind === 'ready' && reserve ? goals.value.find((g) => g.id === reserve.goal_id) ?? null : null;
  const [mode, setMode] = useState<'months' | 'amount'>('months');
  const [months, setMonths] = useState('3'), [amount, setAmount] = useState(''), [holder, setHolder] = useState<Holder>('goal');
  const [saved, setSaved] = useState('0'), [monthly, setMonthly] = useState('');
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  const reserveKey = reserve ? `${reserve.goal_id}:${reserve.target}:${reserve.months ?? ''}` : `none:${chosen}:${ready ? 1 : 0}:${suggestion ? 1 : 0}`;
  useEffect(() => {
    setMode(reserve ? reserve.mode : suggestion ? 'months' : 'amount');
    setMonths(String(reserve?.months ?? ready?.default_months ?? 3));
    setAmount(reserve ? reserve.target : '');
    setHolder(reserve?.account_id ?? 'goal');
    setSaved(reserve && reserve.held_in === 'goal' ? reserve.saved : '0');
    setMonthly(reserve?.monthly_contribution ?? '');
    setError(null);
    // Reset the form when the reserve or the currency changes, not on every render.
  }, [reserveKey]); // eslint-disable-line react-hooks/exhaustive-deps
  if (reserves.kind === 'loading' || goals.kind === 'loading') return <Notice busy>{t('finance.loading')}</Notice>;
  if (reserves.kind !== 'ready') return <LoadError message={t('fset.error', { message: reserves.message })} retry={retry} />;
  if (goals.kind !== 'ready') return <LoadError message={t('fset.error', { message: goals.kind === 'error' || goals.kind === 'denied' ? goals.message : '' })} retry={retry} />;
  const essential = reserve?.essential_monthly ?? suggestion?.essential_monthly ?? null;
  const target = mode === 'months' ? monthsTarget(essential, Number(months), chosen) : null;
  const accountList = accounts.kind === 'ready' ? accounts.value.filter((a) => !a.archived && a.currency === chosen && ['bank', 'cash', 'investment'].includes(a.kind)) : [];
  const currencies = [...new Set([base, ...(accounts.kind === 'ready' ? accounts.value.filter((a) => !a.archived).map((a) => a.currency) : []), ...ready!.reserves.map((r) => r.currency)])].sort();
  async function submit(event: FormEvent) {
    event.preventDefault();
    let value: string;
    if (mode === 'months') {
      const count = Number(months);
      if (!Number.isInteger(count) || count < 1 || count > 24) { setError(t('fset.reserve.monthsError')); return; }
      if (!target) { setError(t('fset.reserve.noEssentials')); return; }
      value = target;
    } else {
      const parsed = parseAmount(amount, chosen, { signed: false });
      if (!parsed.ok || Number(parsed.value) <= 0) { setError(parsed.ok ? t('fset.reserve.amountError') : parsed.error); return; }
      value = parsed.value;
    }
    const savedAmount = holder === 'goal' ? parseAmount(saved || '0', chosen, { signed: false }) : { ok: true as const, value: '0' };
    if (!savedAmount.ok) { setError(savedAmount.error); return; }
    const contribution = monthly.trim() ? parseAmount(monthly, chosen, { signed: false }) : null;
    if (contribution && !contribution.ok) { setError(contribution.error); return; }
    const body: FinanceGoalInput = {
      name: goal?.name ?? t('fset.reserve.defaultName'), target_amount: value, saved_amount: savedAmount.value, currency: chosen,
      target_on: goal?.target_on ?? null, account_id: holder === 'goal' ? null : holder, monthly_contribution: contribution && contribution.ok ? contribution.value : null,
      purpose: 'emergency', reserve_months: mode === 'months' ? Number(months) : 0, notes: goal?.notes ?? null,
    };
    setBusy(true); setError(null);
    try { if (reserve) await api.updateGoal(reserve.goal_id, body); else await api.createGoal(body); onSaved(); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <div className="finReserveSettings">
    {reserve && <ReserveCard reserves={reserves} currency={chosen} onRetry={retry} onOpenSettings={() => document.getElementById('fset-reserve-form')?.focus()} />}
    {reserve && targetDrifted(reserve) && <p className="muted">{t('fset.reserve.drift', { amount: isolate(formatAmount(reserve.recalculated_target ?? '0', chosen)) })}</p>}
    <form id="fset-reserve-form" tabIndex={-1} className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <div className="cmFormGrid">
        <label className="field"><span>{t('finance.form.currency')}</span>
          <select value={chosen} onChange={(e) => setCurrency(e.target.value)}>{currencies.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
        <label className="field"><span>{t('fset.reserve.mode')}</span>
          <select value={mode} onChange={(e) => setMode(e.target.value as 'months' | 'amount')}>
            <option value="months">{t('fset.reserve.modeMonths')}</option><option value="amount">{t('fset.reserve.modeAmount')}</option></select></label>
        {mode === 'months' ? <label className="field"><span>{t('fset.reserve.months')}</span><input type="number" min={1} max={24} value={months} onChange={(e) => setMonths(e.target.value)} dir="ltr" /></label>
          : <label className="field"><span>{t('fset.reserve.amount')}</span><input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" dir="ltr" /></label>}
        <label className="field"><span>{t('fset.reserve.holder')}</span>
          <select value={holder} onChange={(e) => setHolder(e.target.value)}>
            <option value="goal">{t('fset.reserve.inGoal')}</option>
            {accountList.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select></label>
        {holder === 'goal' && <label className="field"><span>{t('fset.reserve.saved')}</span><input value={saved} onChange={(e) => setSaved(e.target.value)} inputMode="decimal" dir="ltr" /></label>}
        <label className="field"><span>{t('fset.reserve.monthly')}</span><input value={monthly} onChange={(e) => setMonthly(e.target.value)} inputMode="decimal" dir="ltr" placeholder="-" /></label>
      </div>
      {mode === 'months' && <p className="muted finHelp">{essential
        ? t('fset.reserve.target', { amount: target ? isolate(formatAmount(target, chosen)) : '-', essential: isolate(formatAmount(essential, chosen)) })
        : t('fset.reserve.noEssentials')}</p>}
      {mode === 'months' && target && <p className="finReserveFigure"><Money value={target} currency={chosen} /></p>}
      <FormError id={errorId} text={error} />
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('fset.saving') : reserve ? t('fset.reserve.update') : t('fset.reserve.create')}</button></div>
    </form>
  </div>;
}
