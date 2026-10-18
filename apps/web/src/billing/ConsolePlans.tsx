// The plan catalog in the billing console: create plans, edit price, trial, limits and features,
// publish them on the pricing page, and archive plans (existing subscriptions keep them).
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { Archive, Pencil, Plus, RefreshCw, RotateCcw, Tags } from 'lucide-react';
import * as b from '../lib/billingApi';
import { LIMIT_KEYS, formatMoney, meterKey, minorToInput, parseMoney, type AdminPlan, type Interval, type Limits } from '../lib/billing';
import { describeError } from '../lib/errors';
import { t } from '../lib/i18n';
import { Modal } from '../Dialog';
import { Badge, Empty, Note, priceLine } from './parts';

export function ConsolePlans() {
  const [plans, setPlans] = useState<AdminPlan[] | null>(null), [problem, setProblem] = useState<string | null>(null);
  const [editing, setEditing] = useState<AdminPlan | 'new' | null>(null), [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const load = useCallback(async () => { setProblem(null); try { setPlans(await b.listCatalog()); } catch (e) { setProblem(describeError(e)); } }, []);
  useEffect(() => { void load(); }, [load]);
  const toggle = async (plan: AdminPlan, patch: Partial<AdminPlan>, text: string) => {
    setFlash(null);
    try { await b.updatePlan(plan.id, patch); setFlash({ text }); await load(); } catch (e) { setFlash({ text: t('billing.failed', { message: describeError(e) }), error: true }); }
  };
  return <section className="surface blPanel" aria-labelledby="console-plans-title">
    <div className="blHead"><div><h2 id="console-plans-title">{t('billing.plans.title')}</h2><p className="muted">{t('billing.plans.lead')}</p></div>
      <div className="actions blInline"><button type="button" aria-haspopup="dialog" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('billing.plans.new')}</button></div></div>
    {flash && <Note error={flash.error}>{flash.text}</Note>}
    {problem && <div className="blRetry"><Note error>{t('billing.plans.failed', { message: problem })}</Note><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
    {plans === null && !problem && <Note>{t('billing.plans.loading')}</Note>}
    {plans?.length === 0 && <Empty icon={<Tags aria-hidden="true" />} title={t('billing.plans.none')} text={t('billing.plans.noneText')} action={<button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('billing.plans.new')}</button>} />}
    {plans && plans.length > 0 && <div className="pane dataTableWrap"><table className="dataTable blTable">
      <caption className="visuallyHidden">{t('billing.plans.title')}</caption>
      <thead><tr><th scope="col">{t('billing.col.plan')}</th><th scope="col">{t('billing.plans.price')}</th><th scope="col" className="optional">{t('billing.plans.trial')}</th><th scope="col">{t('billing.col.status')}</th><th scope="col"><span className="visuallyHidden">{t('billing.col.actions')}</span></th></tr></thead>
      <tbody>{plans.map((plan) => <tr key={plan.id}>
        <th scope="row"><strong dir="auto">{plan.name}</strong><small dir="ltr">{plan.code}</small></th>
        <td>{priceLine(plan)}</td>
        <td className="optional">{t('billing.trialDays', { count: plan.trial_days })}</td>
        <td><div className="blBadges">{!plan.active ? <Badge tone="neutral">{t('billing.plans.archived')}</Badge> : plan.published ? <Badge tone="success">{t('billing.plans.published')}</Badge> : <Badge tone="warning">{t('billing.plans.hidden')}</Badge>}</div></td>
        <td><div className="cellActions">
          <button type="button" className="secondary" onClick={() => setEditing(plan)} aria-label={t('billing.plans.editOf', { name: plan.name })}><Pencil aria-hidden="true" />{t('billing.plans.edit')}</button>
          {plan.active ? <button type="button" className="secondary" onClick={() => void toggle(plan, { active: false, published: false }, t('billing.plans.archivedDone', { name: plan.name }))} aria-label={t('billing.plans.archiveOf', { name: plan.name })}><Archive aria-hidden="true" />{t('billing.plans.archive')}</button>
            : <button type="button" className="secondary" onClick={() => void toggle(plan, { active: true }, t('billing.plans.restored', { name: plan.name }))} aria-label={t('billing.plans.restoreOf', { name: plan.name })}><RotateCcw aria-hidden="true" />{t('billing.plans.restore')}</button>}
        </div></td>
      </tr>)}</tbody>
    </table></div>}
    {editing && <PlanForm plan={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={(saved, created) => { setEditing(null); setFlash({ text: created ? t('billing.plans.created', { name: saved.name }) : t('billing.plans.saved', { name: saved.name }) }); void load(); }} />}
  </section>;
}

function PlanForm({ plan, onClose, onSaved }: { plan: AdminPlan | null; onClose: () => void; onSaved: (plan: AdminPlan, created: boolean) => void }) {
  const currencyOf = plan?.currency ?? 'SAR';
  const [code, setCode] = useState(plan?.code ?? ''), [name, setName] = useState(plan?.name ?? ''), [description, setDescription] = useState(plan?.description ?? '');
  const [currency, setCurrency] = useState(currencyOf), [price, setPrice] = useState(plan ? minorToInput(plan.price_minor, currencyOf) : ''), [interval, setPeriod] = useState<Interval>(plan?.interval ?? 'month');
  const [trial, setTrial] = useState(String(plan?.trial_days ?? 14)), [perSeat, setPerSeat] = useState(plan?.per_seat ?? false), [published, setPublished] = useState(plan?.published ?? false);
  const [sort, setSort] = useState(String(plan?.sort_order ?? 0)), [features, setFeatures] = useState((plan?.features ?? []).join('\n'));
  const [limits, setLimits] = useState<Record<string, string>>(() => Object.fromEntries(LIMIT_KEYS.map((key) => [key, plan?.limits?.[key] == null ? '' : String(plan.limits[key])])));
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const formId = useId();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const minor = price.trim() === '0' ? 0 : parseMoney(price, currency);
    if (!plan && !/^[a-z0-9][a-z0-9_-]{1,59}$/.test(code)) { setProblem(t('billing.plans.codeInvalid')); return; }
    if (!name.trim()) { setProblem(t('billing.plans.nameRequired')); return; }
    if (!/^[A-Z]{3}$/.test(currency)) { setProblem(t('billing.plans.currencyInvalid')); return; }
    if (minor === null) { setProblem(t('billing.console.amountInvalid')); return; }
    const trialDays = Number(trial), sortOrder = Number(sort);
    if (!Number.isInteger(trialDays) || trialDays < 0 || trialDays > 365) { setProblem(t('billing.console.trialRange')); return; }
    const parsed: Limits = {};
    for (const key of LIMIT_KEYS) {
      const raw = limits[key].trim();
      if (!raw) { parsed[key] = null; continue; }
      const value = Number(raw);
      if (!Number.isInteger(value) || value < 0) { setProblem(t('billing.plans.limitInvalid', { what: t(meterKey(key)) })); return; }
      parsed[key] = value;
    }
    const lines = features.split('\n').map((line) => line.trim()).filter(Boolean);
    if (lines.length > 20) { setProblem(t('billing.plans.tooManyFeatures')); return; }
    const common = { name: name.trim(), description: description.trim(), price_minor: minor, trial_days: trialDays, per_seat: perSeat, limits: parsed, features: lines, published, sort_order: Number.isInteger(sortOrder) ? sortOrder : 0 };
    setBusy(true); setProblem(null);
    try {
      if (plan) onSaved(await b.updatePlan(plan.id, common), false);
      else onSaved(await b.createPlan({ ...common, code, currency, interval, active: true }), true);
    } catch (e) { setProblem(t('billing.failed', { message: describeError(e) })); }
    finally { setBusy(false); }
  };
  return <Modal title={plan ? t('billing.plans.editTitle', { name: plan.name }) : t('billing.plans.new')} className="formDialog blWideDialog" onClose={onClose}><form onSubmit={(e) => void submit(e)} noValidate>
    <div className="blTwo">
      <label className="field" htmlFor={`${formId}-name`}><span>{t('billing.plans.name')}</span><input id={`${formId}-name`} value={name} maxLength={120} dir="auto" onChange={(e) => setName(e.target.value)} required /></label>
      <label className="field" htmlFor={`${formId}-code`}><span>{t('billing.plans.code')}</span><input id={`${formId}-code`} value={code} dir="ltr" disabled={Boolean(plan)} maxLength={60} placeholder="business" onChange={(e) => setCode(e.target.value.toLowerCase())} required aria-describedby={`${formId}-code-hint`} /></label>
    </div>
    <small className="muted" id={`${formId}-code-hint`}>{plan ? t('billing.plans.codeFixed') : t('billing.plans.codeHint')}</small>
    <label className="field" htmlFor={`${formId}-desc`}><span>{t('billing.plans.description')}</span><textarea id={`${formId}-desc`} rows={2} maxLength={500} dir="auto" value={description} onChange={(e) => setDescription(e.target.value)} /></label>
    <div className="blThree">
      <label className="field" htmlFor={`${formId}-price`}><span>{t('billing.plans.priceIn', { currency })}</span><input id={`${formId}-price`} inputMode="decimal" dir="ltr" value={price} placeholder="299.00" onChange={(e) => setPrice(e.target.value)} required /></label>
      <label className="field" htmlFor={`${formId}-currency`}><span>{t('billing.plans.currency')}</span><input id={`${formId}-currency`} value={currency} dir="ltr" maxLength={3} disabled={Boolean(plan)} onChange={(e) => setCurrency(e.target.value.toUpperCase())} /></label>
      <label className="field" htmlFor={`${formId}-interval`}><span>{t('billing.plans.interval')}</span><select id={`${formId}-interval`} value={interval} disabled={Boolean(plan)} onChange={(e) => setPeriod(e.target.value as Interval)}><option value="month">{t('billing.plans.monthly')}</option><option value="year">{t('billing.plans.yearly')}</option></select></label>
    </div>
    <div className="blTwo">
      <label className="field" htmlFor={`${formId}-trial`}><span>{t('billing.console.trialDays')}</span><input id={`${formId}-trial`} type="number" min={0} max={365} value={trial} onChange={(e) => setTrial(e.target.value)} /></label>
      <label className="field" htmlFor={`${formId}-sort`}><span>{t('billing.plans.sort')}</span><input id={`${formId}-sort`} type="number" min={0} max={1000} value={sort} onChange={(e) => setSort(e.target.value)} /></label>
    </div>
    <fieldset className="blFieldset"><legend>{t('billing.plans.limits')}</legend><p className="muted blSmall">{t('billing.plans.limitsHint')}</p>
      <div className="blThree">{LIMIT_KEYS.map((key) => <label key={key} className="field" htmlFor={`${formId}-${key}`}><span>{t(meterKey(key))}{key === 'storage_gb' ? ' (GB)' : ''}</span>
        <input id={`${formId}-${key}`} type="number" min={0} value={limits[key]} placeholder={t('billing.plans.unlimited')} onChange={(e) => setLimits({ ...limits, [key]: e.target.value })} /></label>)}</div>
    </fieldset>
    <label className="field" htmlFor={`${formId}-features`}><span>{t('billing.plans.features')}</span><textarea id={`${formId}-features`} rows={4} dir="auto" value={features} onChange={(e) => setFeatures(e.target.value)} aria-describedby={`${formId}-features-hint`} /></label>
    <small className="muted" id={`${formId}-features-hint`}>{t('billing.plans.featuresHint')}</small>
    <label className="blCheck"><input type="checkbox" checked={perSeat} onChange={(e) => setPerSeat(e.target.checked)} /><span>{t('billing.plans.perSeat')}</span></label>
    <label className="blCheck"><input type="checkbox" checked={published} onChange={(e) => setPublished(e.target.checked)} /><span>{t('billing.plans.publish')}</span></label>
    {price && parseMoney(price, currency) !== null && <p className="muted blSmall">{t('billing.plans.previewPrice', { price: formatMoney(parseMoney(price, currency), currency) })}</p>}
    {problem && <Note error>{problem}</Note>}
    <div className="actions"><button type="submit" disabled={busy}>{busy ? t('common.loading') : plan ? t('billing.plans.save') : t('billing.plans.create')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
  </form></Modal>;
}
