// Business catalog (docs/business.md#catalog): products and service packages with priced versions and
// cost assumptions. Nothing is seeded: the owner enters every product, price and cost. Members read.
import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import type { BusinessOffer, BusinessProduct, BusinessProductDetail, BusinessSetup, ProductKind, ProductMaturity } from '@anum/contracts';
import { t } from './lib/i18n';
import {
  CURRENCIES, KINDS, MATURITIES, PRICE_TYPES, emptyOfferDraft, formatMoney, kindLabel, maturityLabel, offerBody, priceTypeLabel, type OfferDraft,
} from './lib/business';
import * as b from './lib/businessApi';
import { Drawer, Modal } from './Dialog';
import { Badge, Empty, Note, failure, when } from './BusinessParts';

export function priceText(offer: BusinessOffer | null): string {
  if (!offer) return t('biz.catalog.noPrice');
  if (offer.price_type === 'quote') return priceTypeLabel('quote');
  const min = formatMoney(offer.price_min, offer.currency);
  if (offer.price_type === 'range') return t('biz.catalog.range', { min, max: formatMoney(offer.price_max, offer.currency) });
  if (offer.price_type === 'from') return t('biz.catalog.from', { min });
  if (offer.price_type === 'hourly') return t('biz.catalog.hourly', { min });
  return min;
}

export function CatalogTab({ setup, products, loading, error, onReload }: { setup: BusinessSetup; products: BusinessProduct[]; loading: boolean; error: string | null; onReload: () => void }) {
  const [open, setOpen] = useState<string | null>(null), [creating, setCreating] = useState(false);
  const manage = setup.permissions.manage;
  return <section className="bzSection" aria-label={t('biz.tab.catalog')}>
    <div className="bzToolbar"><p className="muted bzHint">{t('biz.catalog.help')}</p>
      {manage && <div className="bzToolbarEnd"><button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('biz.catalog.new')}</button></div>}</div>
    {!manage && <Note>{t('biz.catalog.ownersOnly')}</Note>}
    {loading && <Note>{t('biz.loading')}</Note>}
    {error && <Note error>{error} <button type="button" className="secondary" onClick={onReload}>{t('common.tryAgain')}</button></Note>}
    {!loading && !error && products.length === 0 && <Empty title={t('biz.catalog.emptyTitle')} text={manage ? t('biz.catalog.emptyOwner') : t('biz.catalog.emptyMember')}
      action={manage ? <button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('biz.catalog.new')}</button> : undefined} />}
    {products.length > 0 && <div className="pane dataTableWrap"><table className="dataTable bzTable">
      <caption className="visuallyHidden">{t('biz.tab.catalog')}</caption>
      <thead><tr><th scope="col">{t('biz.col.product')}</th><th scope="col" className="bzHideSm">{t('biz.col.kind')}</th><th scope="col">{t('biz.col.maturity')}</th><th scope="col" className="num">{t('biz.col.price')}</th><th scope="col" className="num bzHideMd">{t('biz.col.margin')}</th></tr></thead>
      <tbody>{products.map((item) => <tr key={item.id}>
        <th scope="row"><button type="button" className="linkButton" onClick={() => setOpen(item.id)}><span dir="auto">{item.name}</span></button>{item.summary && <small dir="auto">{item.summary}</small>}</th>
        <td className="bzHideSm">{kindLabel(item.kind)}</td>
        <td><Badge tone={item.maturity === 'live' ? 'success' : item.maturity === 'retired' ? 'dangerTone' : 'neutral'}>{maturityLabel(item.maturity)}</Badge></td>
        <td className="num">{priceText(item.active_offer)}</td>
        <td className="num bzHideMd">{item.active_offer ? (item.active_offer.margin_negative ? t('biz.catalog.marginNegative') : formatMoney(item.active_offer.margin_at_price, item.active_offer.currency)) : t('biz.unknown')}</td>
      </tr>)}</tbody></table></div>}
    {creating && <NewProduct onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); onReload(); setOpen(id); }} />}
    {open && <ProductDrawer id={open} manage={manage} onClose={() => setOpen(null)} onChanged={onReload} />}
  </section>;
}

function NewProduct({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const [kind, setKind] = useState<ProductKind>('service_package'), [name, setName] = useState(''), [nameAr, setNameAr] = useState(''), [summary, setSummary] = useState('');
  const [maturity, setMaturity] = useState<ProductMaturity>('idea'), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (name.trim().length < 2) { setProblem(t('biz.form.nameRequired')); return; }
    setBusy(true); setProblem(null);
    try { onCreated((await b.createProduct({ kind, name: name.trim(), name_ar: nameAr.trim(), summary: summary.trim(), maturity })).id); }
    catch (error) { setProblem(failure(error, t('biz.catalog.new'))); setBusy(false); }
  }
  return <Modal title={t('biz.catalog.new')} className="formDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={t('biz.catalog.new')}>
      <label className="field"><span>{t('biz.col.kind')}</span><select value={kind} onChange={(event) => setKind(event.target.value as ProductKind)} data-autofocus>{KINDS.map((value) => <option key={value} value={value}>{kindLabel(value)}</option>)}</select></label>
      <label className="field"><span>{t('biz.catalog.name')}</span><input value={name} onChange={(event) => setName(event.target.value)} required /></label>
      <label className="field"><span>{t('biz.catalog.nameAr')}</span><input value={nameAr} onChange={(event) => setNameAr(event.target.value)} dir="rtl" lang="ar" /></label>
      <label className="field"><span>{t('biz.catalog.summary')}</span><textarea rows={3} value={summary} onChange={(event) => setSummary(event.target.value)} dir="auto" /></label>
      <label className="field"><span>{t('biz.col.maturity')}</span><select value={maturity} onChange={(event) => setMaturity(event.target.value as ProductMaturity)}>{MATURITIES.map((value) => <option key={value} value={value}>{maturityLabel(value)}</option>)}</select></label>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{t('biz.catalog.add')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

function ProductDrawer({ id, manage, onClose, onChanged }: { id: string; manage: boolean; onClose: () => void; onChanged: () => void }) {
  const [item, setItem] = useState<BusinessProductDetail | null>(null), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<OfferDraft | null>(null), [editing, setEditing] = useState<string | null>(null);
  const reload = useCallback(async () => { try { setItem(await b.getProduct(id)); } catch (error) { setProblem(failure(error, t('biz.catalog.load'))); } }, [id]);
  useEffect(() => { void reload(); }, [reload]);
  async function run(what: string, fn: () => Promise<BusinessProductDetail | void>) {
    setBusy(true); setProblem(null);
    try { const next = await fn(); if (next) setItem(next); onChanged(); } catch (error) { setProblem(failure(error, what)); } finally { setBusy(false); }
  }
  if (!item) return <Drawer title={t('biz.catalog.title')} onClose={onClose}>{problem ? <Note error>{problem}</Note> : <Note>{t('biz.loading')}</Note>}</Drawer>;
  function saveVersion(event: FormEvent) {
    event.preventDefault();
    if (!draft) return;
    const body = offerBody(draft);
    if (!body.ok) { setProblem(body.error); return; }
    void run(t('biz.catalog.saveVersion'), async () => { const next = editing ? await b.updateVersion(editing, body.value) : await b.addVersion(item!.id, body.value); setDraft(null); setEditing(null); return next; });
  }
  const set = (key: keyof OfferDraft) => (event: { target: { value: string } }) => setDraft((current) => (current ? { ...current, [key]: event.target.value } : current));
  const hasDraft = item.offers.some((offer) => offer.status === 'draft');
  return <Drawer title={item.name} onClose={onClose}>
    <div className="bzDrawer">
      <div className="bzLeadHead"><Badge tone="neutral">{kindLabel(item.kind)}</Badge>
        {manage ? <label className="bzCompact"><span>{t('biz.col.maturity')}</span><select value={item.maturity} disabled={busy} onChange={(event) => void run(t('biz.col.maturity'), () => b.updateProduct(item.id, { maturity: event.target.value }))}>{MATURITIES.map((value) => <option key={value} value={value}>{maturityLabel(value)}</option>)}</select></label>
          : <Badge tone="neutral">{maturityLabel(item.maturity)}</Badge>}</div>
      {item.name_ar && <p dir="rtl" lang="ar">{item.name_ar}</p>}
      {item.summary && <p dir="auto">{item.summary}</p>}
      {problem && <Note error>{problem}</Note>}
      <section className="bzBlock" aria-labelledby="bz-versions"><h3 id="bz-versions">{t('biz.catalog.versions')}</h3>
        {item.offers.length === 0 && <p className="muted">{t('biz.catalog.noVersions')}</p>}
        <ul className="bzOffers">{item.offers.map((offer) => <li key={offer.id}>
          <div className="bzMilestoneHead"><strong>{t('biz.catalog.version', { version: offer.version })}</strong><Badge tone={offer.status === 'active' ? 'success' : offer.status === 'draft' ? 'warning' : 'neutral'}>{t(`biz.offerStatus.${offer.status}`)}</Badge><span>{priceText(offer)}</span></div>
          <p className="muted">{t('biz.catalog.costLine', { cost: formatMoney(offer.estimated_cost, offer.currency), hours: offer.cost.labor_hours })}{offer.margin_negative && <> · <span className="bzBlocked">{t('biz.catalog.marginNegative')}</span></>}</p>
          {offer.deliverables.length > 0 && <ul className="bzBullets">{offer.deliverables.map((value) => <li key={value} dir="auto">{value}</li>)}</ul>}
          {offer.limitations.length > 0 && <p className="muted" dir="auto">{t('biz.catalog.limits', { list: offer.limitations.join('; ') })}</p>}
          {offer.activated_at && <small className="muted">{t('biz.catalog.activated', { when: when(offer.activated_at) })}</small>}
          {manage && <span className="bzInline">
            {offer.status === 'draft' && <><button type="button" className="secondary" disabled={busy} onClick={() => { setEditing(offer.id); setDraft({ currency: offer.currency, priceType: offer.price_type, priceMin: offer.price_min?.toString() ?? '', priceMax: offer.price_max?.toString() ?? '', deliverables: offer.deliverables.join('\n'), limitations: offer.limitations.join('\n'), laborHours: String(offer.cost.labor_hours), hourlyCost: String(offer.cost.hourly_cost), externalCosts: String(offer.cost.external_costs), contingency: String(offer.cost.contingency_pct), notes: offer.notes }); }}>{t('biz.catalog.edit')}</button>
              <button type="button" disabled={busy} onClick={() => void run(t('biz.catalog.activate'), () => b.activateVersion(offer.id))}>{t('biz.catalog.activate')}</button></>}
            {offer.status !== 'retired' && <button type="button" className="secondary" disabled={busy} onClick={() => void run(t('biz.catalog.retire'), () => b.retireVersion(offer.id))}>{offer.status === 'draft' ? t('biz.catalog.deleteDraft') : t('biz.catalog.retire')}</button>}
          </span>}
        </li>)}</ul>
        {manage && !draft && !hasDraft && <button type="button" className="secondary" onClick={() => { setEditing(null); setDraft(emptyOfferDraft()); }}><Plus aria-hidden="true" />{t('biz.catalog.newVersion')}</button>}
        {draft && <form className="bzForm" onSubmit={saveVersion} aria-label={t('biz.catalog.versionForm')}>
          <div className="bzGrid3">
            <label className="field"><span>{t('biz.catalog.priceType')}</span><select value={draft.priceType} onChange={set('priceType')}>{PRICE_TYPES.map((value) => <option key={value} value={value}>{priceTypeLabel(value)}</option>)}</select></label>
            <label className="field"><span>{t('biz.field.currency')}</span><select value={draft.currency} onChange={set('currency')}>{CURRENCIES.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
            {draft.priceType !== 'quote' && <label className="field"><span>{draft.priceType === 'range' ? t('biz.catalog.priceMin') : t('biz.catalog.priceField')}</span><input inputMode="decimal" value={draft.priceMin} onChange={set('priceMin')} /></label>}
            {draft.priceType === 'range' && <label className="field"><span>{t('biz.catalog.priceMax')}</span><input inputMode="decimal" value={draft.priceMax} onChange={set('priceMax')} /></label>}
          </div>
          <fieldset className="bzFieldset"><legend>{t('biz.catalog.costAssumptions')}</legend><div className="bzGrid2">
            <label className="field"><span>{t('biz.catalog.hours')}</span><input inputMode="decimal" value={draft.laborHours} onChange={set('laborHours')} /></label>
            <label className="field"><span>{t('biz.catalog.hourlyCost')}</span><input inputMode="decimal" value={draft.hourlyCost} onChange={set('hourlyCost')} /></label>
            <label className="field"><span>{t('biz.catalog.external')}</span><input inputMode="decimal" value={draft.externalCosts} onChange={set('externalCosts')} /></label>
            <label className="field"><span>{t('biz.catalog.contingency')}</span><input inputMode="decimal" value={draft.contingency} onChange={set('contingency')} /></label>
          </div></fieldset>
          <label className="field"><span>{t('biz.proposal.deliverables')}</span><textarea rows={3} value={draft.deliverables} onChange={set('deliverables')} dir="auto" /></label>
          <label className="field"><span>{t('biz.catalog.limitations')}</span><textarea rows={2} value={draft.limitations} onChange={set('limitations')} dir="auto" /></label>
          <div className="actions"><button type="submit" disabled={busy}>{t('biz.catalog.saveVersion')}</button><button type="button" className="secondary" onClick={() => { setDraft(null); setEditing(null); }}>{t('common.cancel')}</button></div>
        </form>}
      </section>
      {manage && <div className="bzDanger"><button type="button" className="secondary dangerText" disabled={busy} onClick={() => void run(t('biz.catalog.delete'), async () => { await b.deleteProduct(item.id); onClose(); })}><Trash2 aria-hidden="true" />{t('biz.catalog.delete')}</button></div>}
    </div>
  </Drawer>;
}
