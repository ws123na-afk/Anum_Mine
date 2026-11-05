// One purchase's page and the forms of Purchases (docs/purchases.md#web): the purchase form with its lines, the delivery
// (status along the state machine and shipments), the lines, returns with their refunds, the history and the details. A
// payment or a refund is always one of the person's own Finance transactions (or cash recorded once in Finance): linking
// never adds money, and a refund lowers what the purchase cost; it is never income.
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { Archive, ArchiveRestore, History, Link2, ListChecks, Pencil, Plus, Trash2, Truck, Undo2, Unlink } from 'lucide-react';
import type {
  FinanceAccount, PurchaseDetail, PurchaseEvent, PurchaseMoneyLink, PurchasePage, PurchasePaymentCandidate, PurchaseReturn, PurchaseReturnReason,
  PurchaseShipment, PurchaseShipmentStatus,
} from '@anum/contracts';
import { PURCHASE_PAYMENT_METHODS, PURCHASE_RETURN_REASONS, PURCHASE_SHIPMENT_STATUSES } from '@anum/contracts';
import * as api from './lib/purchasesApi';
import {
  PAGE_SIZE, PURCHASE_DETAIL_TABS, decimal, dueLabel, dueText, linesTotal, linkState, linkText, methodLabel, moneyList, newKey, purchaseName, reasonLabel,
  refundLeft, returnStatusLabel, shipmentLabel, statusLabel, today, type PurchaseDetailTab,
} from './lib/purchases';
import { formatAmount } from './lib/finance';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { figure, type Figure } from './lib/pageSummary';
import { ConfirmDialog, Modal } from './Dialog';
import { Empty, FormError, LoadError, Notice, failed, type Load } from './FinanceParts';
import { PageSummary } from './PageParts';
import { Pager, StatusChip } from './PurchasesView';

type Message = { text: string; error?: boolean } | null;
const ORDER_STEPS = ['ordered', 'confirmed', 'shipped', 'out_for_delivery', 'delivered'] as const;

// ---- The purchase form ---------------------------------------------------------------------------------

interface Line { id: string | null; description: string; quantity: string; unit_price: string }

export function PurchaseForm({ purchase, onClose, onSaved }: { purchase?: PurchaseDetail; onClose: () => void; onSaved: (p: PurchaseDetail) => void }) {
  const [f, setF] = useState(() => ({
    ownership: purchase?.ownership ?? 'personal', merchant: purchase?.merchant ?? '', order_ref: purchase?.order_ref ?? '', ordered_on: purchase?.ordered_on ?? today(),
    currency: purchase?.currency ?? '', total: purchase?.total ?? '', tax_amount: purchase?.tax_amount ?? '', shipping_amount: purchase?.shipping_amount ?? '',
    payment_method: purchase?.payment_method ?? '', expected_delivery_on: purchase?.expected_delivery_on ?? '', address_ref: purchase?.address_ref ?? '',
    warranty_until: purchase?.warranty_until ?? '', return_window_until: purchase?.return_window_until ?? '', document_id: purchase?.document_id ?? '',
    notes: purchase?.notes ?? '', status: 'ordered',
  }));
  const [lines, setLines] = useState<Line[]>(() => (purchase?.items ?? []).map((i) => ({ id: i.id, description: i.description, quantity: i.quantity, unit_price: i.unit_price ?? '' })));
  const [key] = useState(() => newKey('purchase'));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (name: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [name]: e.target.value });
  const setLine = (index: number, patch: Partial<Line>) => setLines(lines.map((line, i) => (i === index ? { ...line, ...patch } : line)));
  const sum = linesTotal(lines);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!f.merchant.trim()) return setError(t('pur.form.needMerchant'));
    if (f.currency && !/^[A-Za-z]{3}$/.test(f.currency)) return setError(t('pur.form.badCurrency'));
    const money = { total: f.total ? decimal(f.total) : null, tax_amount: f.tax_amount ? decimal(f.tax_amount) : null, shipping_amount: f.shipping_amount ? decimal(f.shipping_amount) : null };
    if (Object.entries(money).some(([name, value]) => (f as Record<string, string>)[name] && value === null)) return setError(t('pur.form.badNumber'));
    const items = [];
    for (const [index, line] of lines.entries()) {
      if (!line.description.trim()) return setError(t('pur.form.lineNeeds', { n: index + 1 }));
      const quantity = decimal(line.quantity || '1', 3);
      const price = line.unit_price ? decimal(line.unit_price) : null;
      if (!quantity || Number(quantity) <= 0 || (line.unit_price && price === null)) return setError(t('pur.form.lineNumbers', { n: index + 1 }));
      items.push({ ...(line.id ? { id: line.id } : {}), description: line.description.trim(), quantity, unit_price: price });
    }
    const body = {
      merchant: f.merchant.trim(), order_ref: f.order_ref.trim() || null, ordered_on: f.ordered_on || null, currency: f.currency ? f.currency.toUpperCase() : null,
      ...money, payment_method: (f.payment_method || null) as never, expected_delivery_on: f.expected_delivery_on || null, address_ref: f.address_ref.trim() || null,
      warranty_until: f.warranty_until || null, return_window_until: f.return_window_until || null, document_id: f.document_id.trim() || null,
      notes: f.notes.trim() || null, items,
    };
    setBusy(true); setError(null);
    try {
      const saved = purchase
        ? await api.updatePurchase(purchase.id, { ...body, currency: body.currency ?? purchase.currency, ordered_on: body.ordered_on ?? purchase.ordered_on, version: purchase.version })
        : await api.createPurchase({ ...body, ownership: f.ownership as 'personal' | 'business', status: f.status as never }, key);
      onSaved(saved);
    } catch (err) { setError(describeError(err)); } finally { setBusy(false); }
  };
  return <Modal title={purchase ? t('pur.form.editTitle') : t('pur.form.addTitle')} className="purDialog" onClose={onClose}>
    <form className="purForm" noValidate onSubmit={(e) => void submit(e)} aria-describedby="pur-form-error">
      {!purchase && <fieldset className="purFieldset"><legend>{t('pur.form.ownership')}</legend>
        {(['personal', 'business'] as const).map((o) => <label key={o} className="purCheck"><input type="radio" name="pur-own" checked={f.ownership === o} onChange={() => setF({ ...f, ownership: o })} />{t(`pur.own.${o}.long` as MessageKey)}</label>)}
      </fieldset>}
      <div className="purTwo">
        <label className="field"><span>{t('pur.form.merchant')}</span><input dir="auto" required maxLength={120} value={f.merchant} onChange={set('merchant')} /></label>
        <label className="field"><span>{t('pur.form.orderRef')}</span><input dir="ltr" maxLength={80} value={f.order_ref} onChange={set('order_ref')} /></label>
        <label className="field"><span>{t('pur.form.orderedOn')}</span><input type="date" max={today()} value={f.ordered_on} onChange={set('ordered_on')} /></label>
        <label className="field"><span>{t('pur.form.currency')}</span><input dir="ltr" maxLength={3} placeholder={t('pur.form.currencyHint')} value={f.currency} onChange={set('currency')} /></label>
        <label className="field"><span>{t('pur.form.total')}</span><input inputMode="decimal" value={f.total} placeholder={sum ?? ''} onChange={set('total')} /></label>
        <label className="field"><span>{t('pur.form.method')}</span><select value={f.payment_method} onChange={set('payment_method')}>
          <option value="">-</option>{PURCHASE_PAYMENT_METHODS.map((m) => <option key={m} value={m}>{methodLabel(m)}</option>)}</select></label>
        <label className="field"><span>{t('pur.form.tax')}</span><input inputMode="decimal" value={f.tax_amount} onChange={set('tax_amount')} /></label>
        <label className="field"><span>{t('pur.form.shipping')}</span><input inputMode="decimal" value={f.shipping_amount} onChange={set('shipping_amount')} /></label>
        {!purchase && <label className="field"><span>{t('pur.form.status')}</span><select value={f.status} onChange={set('status')}>
          {ORDER_STEPS.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}</select></label>}
        <label className="field"><span>{t('pur.form.expected')}</span><input type="date" value={f.expected_delivery_on} onChange={set('expected_delivery_on')} /></label>
      </div>
      <p className="muted purNote">{t('pur.form.moneyNote')}</p>
      <fieldset className="purFieldset"><legend>{t('pur.form.lines')}</legend>
        {lines.length === 0 && <p className="muted purNote">{t('pur.form.noLines')}</p>}
        {lines.map((line, index) => <div className="purLine" key={line.id ?? `new-${index}`}>
          <label className="field"><span>{t('pur.form.lineDescription', { n: index + 1 })}</span><input dir="auto" maxLength={200} value={line.description} onChange={(e) => setLine(index, { description: e.target.value })} /></label>
          <label className="field"><span>{t('pur.form.lineQuantity')}</span><input inputMode="decimal" value={line.quantity} onChange={(e) => setLine(index, { quantity: e.target.value })} /></label>
          <label className="field"><span>{t('pur.form.linePrice')}</span><input inputMode="decimal" value={line.unit_price} onChange={(e) => setLine(index, { unit_price: e.target.value })} /></label>
          <button type="button" className="secondary danger" aria-label={t('pur.form.lineRemove', { n: index + 1 })} onClick={() => setLines(lines.filter((_, i) => i !== index))}><Trash2 aria-hidden="true" /></button>
        </div>)}
        <div className="purBar"><button type="button" className="secondary" onClick={() => setLines([...lines, { id: null, description: '', quantity: '1', unit_price: '' }])}><Plus aria-hidden="true" />{t('pur.form.lineAdd')}</button>
          {sum && <span className="muted" dir="auto">{t('pur.form.linesSum', { amount: sum })}</span>}</div>
      </fieldset>
      <fieldset className="purFieldset"><legend>{t('pur.form.after')}</legend><div className="purTwo">
        <label className="field"><span>{t('pur.form.returnWindow')}</span><input type="date" value={f.return_window_until} onChange={set('return_window_until')} /></label>
        <label className="field"><span>{t('pur.form.warranty')}</span><input type="date" value={f.warranty_until} onChange={set('warranty_until')} /></label>
        <label className="field"><span>{t('pur.form.address')}</span><input dir="auto" maxLength={80} placeholder={t('pur.form.addressHint')} value={f.address_ref} onChange={set('address_ref')} /></label>
        <label className="field"><span>{t('pur.form.document')}</span><input dir="ltr" maxLength={80} placeholder="doc_..." value={f.document_id} onChange={set('document_id')} /></label>
      </div><p className="muted purNote">{t('pur.form.addressNote')}</p></fieldset>
      <label className="field"><span>{t('pur.form.notes')}</span><textarea dir="auto" maxLength={1000} rows={2} value={f.notes} onChange={set('notes')} /></label>
      <FormError id="pur-form-error" text={error} />
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('pur.saving') : t('pur.save')}</button><button type="button" className="secondary" disabled={busy} onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ---- One purchase --------------------------------------------------------------------------------------

const TAB_ICONS: Record<PurchaseDetailTab, ReactNode> = {
  delivery: <Truck aria-hidden="true" />, items: <ListChecks aria-hidden="true" />, returns: <Undo2 aria-hidden="true" />, activity: <History aria-hidden="true" />,
  details: <Pencil aria-hidden="true" />,
};

export function PurchaseDetailView({ purchaseId, onBack, onDeleted }: { purchaseId: string; onBack: () => void; onDeleted: () => void }) {
  const [data, setData] = useState<Load<PurchaseDetail>>({ kind: 'loading' });
  const [tab, setTab] = useState<PurchaseDetailTab>('delivery');
  const [tick, setTick] = useState(0);
  const [message, setMessage] = useState<Message>(null);
  const load = useCallback(async () => {
    try { setData({ kind: 'ready', value: await api.getPurchase(purchaseId) }); } catch (e) { setData(failed(e)); }
  }, [purchaseId]);
  useEffect(() => { void load(); }, [load, tick]);
  const changed = (text?: string) => { if (text) setMessage({ text }); setTick((n) => n + 1); };
  const back = <button type="button" className="secondary purBack" onClick={onBack}>{t('pur.back')}</button>;
  if (data.kind === 'loading') return <Notice busy>{t('pur.loading')}</Notice>;
  if (data.kind === 'denied' || data.kind === 'error') return <><LoadError message={t('pur.error.purchase', { message: data.message })} retry={() => changed()} />{back}</>;
  const p = data.value;
  const figures: Figure[] = [
    figure('status', t('pur.fig.status'), { state: 'ready' }, () => ({ value: statusLabel(p.status), hint: p.expected_delivery_on ? t('pur.fig.expected', { date: p.expected_delivery_on }) : t('pur.fig.ordered', { date: p.ordered_on }) })),
    figure('total', t('pur.fig.total'), { state: 'ready' }, () => ({ value: p.total ? formatAmount(p.total, p.currency) : (p.items_total ? formatAmount(p.items_total, p.currency) : '-'), hint: t('pur.fig.totalHint') })),
    figure('paid', t('pur.fig.paid'), { state: 'ready' }, () => ({ value: p.payment ? linkText(p.payment) : '-', hint: p.payment ? t('pur.fig.paidHint') : t('pur.fig.unpaidHint'), tone: linkState(p.payment) === 'needs_review' ? 'warn' : undefined })),
    figure('refunded', t('pur.fig.refunded'), { state: 'ready' }, () => ({ value: moneyList(p.refunded), hint: t('pur.fig.refundedHint') })),
  ];
  const markReviewed = async () => {
    try { await api.updatePurchase(p.id, { version: p.version, needs_review: false }); changed(t('pur.review.done')); } catch (e) { setMessage({ text: describeError(e), error: true }); }
  };
  return <div className="purDetail">
    <div className="purDetailHead">
      {back}
      <div><h2 dir="auto"><bdi>{p.merchant}</bdi>{p.order_ref ? <> · <bdi>{p.order_ref}</bdi></> : null}</h2>
        <p className="muted">{[t(`pur.own.${p.ownership}` as MessageKey), t('pur.fig.ordered', { date: p.ordered_on }), p.source === 'iphone' ? t('pur.fromIphone') : '',
          p.archived_at ? t('pur.archived') : ''].filter(Boolean).join(' · ')}</p>
        <span className="purChips"><StatusChip status={p.status} /></span></div>
    </div>
    {p.needs_review && <div className="notice purReview" role="status"><span>{t('pur.review.text')}</span>
      {p.can_edit && <button type="button" className="secondary" onClick={() => void markReviewed()}>{t('pur.review.mark')}</button>}</div>}
    {p.archived_at && <Notice>{t('pur.archivedNotice')}</Notice>}
    {message && <Notice error={message.error}>{message.text}</Notice>}
    {p.due.length > 0 && <ul className="purDue" aria-label={t('pur.due.title')}>{p.due.map((d) => <li key={`${d.kind}:${d.source_id}`} className={d.days_left < 2 ? 'soon' : ''}>{dueLabel(d)}: {dueText(d)}</li>)}</ul>}
    <PageSummary label={t('pur.detail.summary')} figures={figures} footer={<p className="muted purNote">{t('pur.noPosting')}</p>} />
    <nav className="settingsTabs cmTabs purTabs" aria-label={t('pur.detail.tabs')}>
      {PURCHASE_DETAIL_TABS.map((id) => <button key={id} type="button" aria-current={tab === id ? 'page' : undefined} onClick={() => setTab(id)}>{TAB_ICONS[id]}{t(`pur.dtab.${id}` as MessageKey)}</button>)}
    </nav>
    {tab === 'delivery' && <DeliveryPanel purchase={p} onChanged={changed} />}
    {tab === 'items' && <ItemsPanel purchase={p} onChanged={changed} />}
    {tab === 'returns' && <ReturnsPanel purchase={p} onChanged={changed} />}
    {tab === 'activity' && <ActivityPanel purchase={p} onChanged={changed} />}
    {tab === 'details' && <DetailsPanel purchase={p} onChanged={changed} onDeleted={onDeleted} />}
  </div>;
}

// ---- Moving along the state machine -------------------------------------------------------------------

function MoveForm({ id, allowed, labelOf, onMove }: { id: string; allowed: string[]; labelOf: (s: string) => string; onMove: (to: string, note: string) => Promise<void> }) {
  const [to, setTo] = useState(allowed[0] ?? '');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { setTo(allowed[0] ?? ''); }, [allowed.join(',')]);
  if (allowed.length === 0) return <p className="muted purNote">{t('pur.move.none')}</p>;
  return <form className="purForm purMove" noValidate onSubmit={(e) => { e.preventDefault(); setBusy(true); void onMove(to, note.trim()).finally(() => { setBusy(false); setNote(''); }); }}>
    <label className="field"><span>{t('pur.move.to')}</span><select id={`${id}-to`} value={to} onChange={(e) => setTo(e.target.value)}>
      {allowed.map((s) => <option key={s} value={s}>{labelOf(s)}</option>)}</select></label>
    <label className="field"><span>{t('pur.move.note')}</span><input dir="auto" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} /></label>
    <div className="actions"><button type="submit" disabled={busy || !to}>{busy ? t('pur.saving') : t('pur.move.save')}</button></div>
  </form>;
}

// ---- Delivery: status and shipments --------------------------------------------------------------------

function DeliveryPanel({ purchase, onChanged }: { purchase: PurchaseDetail; onChanged: (text?: string) => void }) {
  const [editing, setEditing] = useState<PurchaseShipment | 'new' | null>(null);
  const [removing, setRemoving] = useState<PurchaseShipment | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);
  const move = async (to: string, note: string) => {
    setMessage(null);
    try { await api.moveStatus(purchase.id, { to, version: purchase.version, note: note || null }); onChanged(t('pur.move.saved', { status: statusLabel(to) })); }
    catch (e) { setMessage({ text: describeError(e), error: true }); }
  };
  const quick = async (shipment: PurchaseShipment, status: PurchaseShipmentStatus) => {
    setMessage(null);
    try { await api.updateShipment(shipment.id, { version: shipment.version, status }); onChanged(t('pur.ship.saved')); } catch (e) { setMessage({ text: describeError(e), error: true }); }
  };
  const remove = async () => {
    if (!removing) return;
    setBusy(true);
    try { await api.deleteShipment(removing.id); setRemoving(null); onChanged(t('pur.ship.deleted')); } catch (e) { setRemoving(null); setMessage({ text: describeError(e), error: true }); } finally { setBusy(false); }
  };
  const locked = Boolean(purchase.archived_at);
  return <div className="purGrid">
    <section className="surface cmPanel" aria-labelledby="pur-status-title">
      <h2 id="pur-status-title">{t('pur.move.title')}</h2>
      <p className="muted purNote">{t('pur.move.rule')}</p>
      <dl className="purFacts">
        <dt>{t('pur.fig.status')}</dt><dd><StatusChip status={purchase.status} /></dd>
        <dt>{t('pur.form.expected')}</dt><dd>{purchase.expected_delivery_on ?? '-'}</dd>
        <dt>{t('pur.form.delivered')}</dt><dd>{purchase.delivered_on ?? '-'}</dd>
        <dt>{t('pur.form.address')}</dt><dd dir="auto">{purchase.address_ref ?? '-'}</dd>
      </dl>
      {message && <Notice error={message.error}>{message.text}</Notice>}
      {!locked && <MoveForm id="pur-move" allowed={purchase.allowed_moves} labelOf={statusLabel} onMove={move} />}
    </section>
    <section className="surface cmPanel" aria-labelledby="pur-ship-title">
      <div className="purBar"><h2 id="pur-ship-title">{t('pur.ship.title')}</h2>
        {!locked && <button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('pur.ship.add')}</button>}</div>
      <p className="muted purNote">{t('pur.ship.rule')}</p>
      {purchase.shipments.length === 0 ? <p className="muted">{t('pur.ship.none')}</p>
        : <ul className="cmList purRows">{purchase.shipments.map((s) => <li key={s.id}>
          <div><strong dir="auto">{s.carrier ?? t('pur.ship.carrierUnknown')}{s.tracking_number ? <> · <bdi dir="ltr">{s.tracking_number}</bdi></> : null}</strong>
            <small>{[shipmentLabel(s.status), s.expected_on ? t('pur.ship.expectedOn', { date: s.expected_on }) : '', s.delivered_on ? t('pur.ship.deliveredOn', { date: s.delivered_on }) : '', s.note ?? ''].filter(Boolean).join(' · ')}</small></div>
          {s.can_edit && <div className="purRowButtons">
            {s.status !== 'delivered' && <button type="button" className="secondary" onClick={() => void quick(s, 'delivered')} aria-label={t('pur.ship.markLabel', { what: s.tracking_number ?? s.carrier ?? '' })}>{t('pur.ship.mark')}</button>}
            <button type="button" className="secondary" aria-label={t('pur.editLabel', { what: s.tracking_number ?? s.carrier ?? '' })} onClick={() => setEditing(s)}><Pencil aria-hidden="true" />{t('pur.edit')}</button>
            <button type="button" className="secondary danger" aria-label={t('pur.deleteLabel', { what: s.tracking_number ?? s.carrier ?? '' })} onClick={() => setRemoving(s)}><Trash2 aria-hidden="true" />{t('pur.delete')}</button>
          </div>}
        </li>)}</ul>}
    </section>
    {editing && <ShipmentForm purchaseId={purchase.id} shipment={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); onChanged(t('pur.ship.saved')); }} />}
    {removing && <ConfirmDialog title={t('pur.ship.deleteTitle')} confirmLabel={t('pur.delete')} danger busy={busy} onConfirm={() => void remove()} onCancel={() => setRemoving(null)}>
      <p>{t('pur.ship.deleteText')}</p></ConfirmDialog>}
  </div>;
}

function ShipmentForm({ purchaseId, shipment, onClose, onSaved }: { purchaseId: string; shipment: PurchaseShipment | null; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ carrier: shipment?.carrier ?? '', tracking_number: shipment?.tracking_number ?? '', status: shipment?.status ?? 'in_transit',
    shipped_on: shipment?.shipped_on ?? '', expected_on: shipment?.expected_on ?? '', delivered_on: shipment?.delivered_on ?? '', note: shipment?.note ?? '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (name: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [name]: e.target.value });
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!f.carrier.trim() && !f.tracking_number.trim()) return setError(t('pur.ship.needOne'));
    const body = { carrier: f.carrier.trim() || null, tracking_number: f.tracking_number.trim() || null, status: f.status as PurchaseShipmentStatus,
      shipped_on: f.shipped_on || null, expected_on: f.expected_on || null, delivered_on: f.delivered_on || null, note: f.note.trim() || null };
    setBusy(true); setError(null);
    try { if (shipment) await api.updateShipment(shipment.id, { ...body, version: shipment.version }); else await api.addShipment(purchaseId, body); onSaved(); }
    catch (err) { setError(describeError(err)); } finally { setBusy(false); }
  };
  return <Modal title={shipment ? t('pur.ship.editTitle') : t('pur.ship.addTitle')} className="purDialog" onClose={onClose}>
    <form className="purForm" noValidate onSubmit={(e) => void submit(e)}>
      <div className="purTwo">
        <label className="field"><span>{t('pur.ship.carrier')}</span><input dir="auto" maxLength={60} value={f.carrier} onChange={set('carrier')} /></label>
        <label className="field"><span>{t('pur.ship.tracking')}</span><input dir="ltr" maxLength={60} value={f.tracking_number} onChange={set('tracking_number')} /></label>
        <label className="field"><span>{t('pur.ship.status')}</span><select value={f.status} onChange={set('status')}>{PURCHASE_SHIPMENT_STATUSES.map((s) => <option key={s} value={s}>{shipmentLabel(s)}</option>)}</select></label>
        <label className="field"><span>{t('pur.ship.shippedOn')}</span><input type="date" max={today()} value={f.shipped_on} onChange={set('shipped_on')} /></label>
        <label className="field"><span>{t('pur.ship.expected')}</span><input type="date" value={f.expected_on} onChange={set('expected_on')} /></label>
        <label className="field"><span>{t('pur.ship.delivered')}</span><input type="date" max={today()} value={f.delivered_on} onChange={set('delivered_on')} /></label>
      </div>
      <label className="field"><span>{t('pur.move.note')}</span><input dir="auto" maxLength={300} value={f.note} onChange={set('note')} /></label>
      <FormError id="pur-ship-error" text={error} />
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('pur.saving') : t('pur.save')}</button><button type="button" className="secondary" disabled={busy} onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ---- Lines -----------------------------------------------------------------------------------------------

function ItemsPanel({ purchase, onChanged }: { purchase: PurchaseDetail; onChanged: (text?: string) => void }) {
  const [editing, setEditing] = useState(false);
  return <section className="surface cmPanel" aria-labelledby="pur-items-title">
    <div className="purBar"><h2 id="pur-items-title">{t('pur.items.title')}</h2>
      {purchase.can_edit && <button type="button" className="secondary" onClick={() => setEditing(true)}><Pencil aria-hidden="true" />{t('pur.items.edit')}</button>}</div>
    {purchase.items.length === 0 ? <Empty icon={<ListChecks aria-hidden="true" />} title={t('pur.items.none')} text={t('pur.items.noneText')}
      action={purchase.can_edit ? <button type="button" onClick={() => setEditing(true)}><Plus aria-hidden="true" />{t('pur.form.lineAdd')}</button> : undefined} />
      : <div className="purTableWrap"><table className="cmTable purTable"><thead><tr><th scope="col">{t('pur.items.description')}</th><th scope="col" className="num">{t('pur.items.quantity')}</th>
        <th scope="col" className="num">{t('pur.items.price')}</th><th scope="col" className="num">{t('pur.items.lineTotal')}</th></tr></thead>
        <tbody>{purchase.items.map((i) => <tr key={i.id}><td dir="auto">{i.description}</td><td className="num">{Number(i.quantity)}</td>
          <td className="num" dir="ltr">{i.unit_price ? formatAmount(i.unit_price, purchase.currency) : '-'}</td>
          <td className="num" dir="ltr">{i.unit_price ? formatAmount((Number(i.unit_price) * Number(i.quantity)).toFixed(2), purchase.currency) : '-'}</td></tr>)}</tbody>
        {purchase.items_total && <tfoot><tr><th scope="row" colSpan={3}>{t('pur.items.sum')}</th><td className="num" dir="ltr">{formatAmount(purchase.items_total, purchase.currency)}</td></tr></tfoot>}
      </table></div>}
    {editing && <PurchaseForm purchase={purchase} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); onChanged(t('pur.saved')); }} />}
  </section>;
}

// ---- Returns and refunds -------------------------------------------------------------------------------

function ReturnsPanel({ purchase, onChanged }: { purchase: PurchaseDetail; onChanged: (text?: string) => void }) {
  const [editing, setEditing] = useState<PurchaseReturn | 'new' | null>(null);
  const [refunding, setRefunding] = useState<PurchaseReturn | null>(null);
  const [removing, setRemoving] = useState<PurchaseReturn | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);
  const canAsk = !purchase.archived_at && !['ordered', 'confirmed', 'shipped', 'out_for_delivery', 'cancelled'].includes(purchase.status);
  const move = (item: PurchaseReturn) => async (to: string, note: string) => {
    setMessage(null);
    try { await api.moveReturn(item.id, { to, version: item.version, note: note || null }); onChanged(t('pur.ret.moved', { status: returnStatusLabel(to) })); }
    catch (e) { setMessage({ text: describeError(e), error: true }); }
  };
  const unlink = async (item: PurchaseReturn, link: PurchaseMoneyLink) => {
    if (!link.link_id) return;
    setMessage(null);
    try { await api.unlinkRefund(item.id, link.link_id); onChanged(t('pur.refund.unlinked')); } catch (e) { setMessage({ text: describeError(e), error: true }); }
  };
  const remove = async () => {
    if (!removing) return;
    setBusy(true);
    try { await api.deleteReturn(removing.id); setRemoving(null); onChanged(t('pur.ret.deleted')); } catch (e) { setRemoving(null); setMessage({ text: describeError(e), error: true }); } finally { setBusy(false); }
  };
  const itemName = (id: string) => purchase.items.find((i) => i.id === id)?.description ?? id;
  return <section className="surface cmPanel" aria-labelledby="pur-returns-title">
    <div className="purBar"><h2 id="pur-returns-title">{t('pur.ret.title')}</h2>
      {canAsk && <button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('pur.ret.add')}</button>}</div>
    <p className="muted purNote">{t('pur.ret.rule')}</p>
    {!canAsk && !purchase.archived_at && <p className="muted purNote">{t('pur.ret.notYet')}</p>}
    {message && <Notice error={message.error}>{message.text}</Notice>}
    {purchase.returns.length === 0 ? <p className="muted">{t('pur.ret.none')}</p>
      : <ul className="purReturns">{purchase.returns.map((r) => <li key={r.id} className="purReturn">
        <div className="purBar"><h3 dir="auto">{reasonLabel(r.reason)} · {r.requested_on}</h3><StatusChip status={r.status} kind="return" /></div>
        <dl className="purFacts">
          <dt>{t('pur.ret.items')}</dt><dd dir="auto">{r.items.length ? r.items.map((l) => `${itemName(l.item_id)} × ${Number(l.quantity)}`).join(', ') : t('pur.ret.wholeOrder')}</dd>
          <dt>{t('pur.ret.amount')}</dt><dd dir="ltr">{r.amount ? formatAmount(r.amount, r.currency) : '-'}</dd>
          <dt>{t('pur.ret.refunded')}</dt><dd dir="ltr">{r.refunded_all.length ? moneyList(r.refunded_all) : formatAmount('0', r.currency)}{refundLeft(r) && refundLeft(r) !== '0.00' ? ` (${t('pur.ret.left', { amount: formatAmount(refundLeft(r)!, r.currency) })})` : ''}</dd>
          {r.deadline_on && <><dt>{t('pur.ret.deadline')}</dt><dd>{r.deadline_on}</dd></>}
          {(r.pickup_on || r.courier || r.tracking_number) && <><dt>{t('pur.ret.pickup')}</dt><dd dir="auto">{[r.pickup_on, r.courier, r.tracking_number].filter(Boolean).join(' · ')}</dd></>}
          {r.confirmation_ref && <><dt>{t('pur.ret.confirmation')}</dt><dd dir="ltr">{r.confirmation_ref}</dd></>}
          {r.expected_refund_on && <><dt>{t('pur.ret.expectedRefund')}</dt><dd>{r.expected_refund_on}</dd></>}
          {r.dispute_note && <><dt>{t('pur.ret.dispute')}</dt><dd dir="auto">{r.dispute_note}</dd></>}
          {r.proof_document_id && <><dt>{t('pur.ret.proof')}</dt><dd><a href="#vault" dir="ltr">{r.proof_document_id}</a></dd></>}
        </dl>
        {r.refunds.length > 0 && <ul className="cmList purRows">{r.refunds.map((link, i) => <li key={link.link_id ?? i}>
          <div><strong><span className={`purChip ${link.status === 'active' ? 'done' : link.status === 'needs_review' ? 'warn' : 'closed'}`}>{linkText(link)}</span></strong>
            <small>{[formatDateTime(link.linked_at), link.recorded_as_cash ? t('pur.pay.cashRecorded') : '', link.original_canonical_id ? t('pur.refund.ofPayment') : ''].filter(Boolean).join(' · ')}</small></div>
          {r.can_refund && link.link_id && link.status !== 'void' && <button type="button" className="secondary danger" onClick={() => void unlink(r, link)}
            aria-label={t('pur.refund.unlinkLabel', { amount: formatAmount(link.amount, link.currency) })}><Unlink aria-hidden="true" />{t('pur.refund.unlink')}</button>}
        </li>)}</ul>}
        {!purchase.archived_at && r.can_edit && <MoveForm id={`ret-${r.id}`} allowed={r.allowed_moves} labelOf={returnStatusLabel} onMove={move(r)} />}
        <div className="purRowButtons">
          {r.can_refund && !['rejected', 'cancelled', 'refunded'].includes(r.status) && <button type="button" className="secondary" onClick={() => setRefunding(r)}><Link2 aria-hidden="true" />{t('pur.refund.link')}</button>}
          {r.can_edit && <button type="button" className="secondary" onClick={() => setEditing(r)} aria-label={t('pur.editLabel', { what: reasonLabel(r.reason) })}><Pencil aria-hidden="true" />{t('pur.edit')}</button>}
          {r.can_edit && <button type="button" className="secondary danger" onClick={() => setRemoving(r)} aria-label={t('pur.deleteLabel', { what: reasonLabel(r.reason) })}><Trash2 aria-hidden="true" />{t('pur.delete')}</button>}
        </div>
      </li>)}</ul>}
    {editing && <ReturnForm purchase={purchase} item={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); onChanged(t('pur.ret.saved')); }} />}
    {refunding && <MoneyDialog mode="refund" targetId={refunding.id} current={null} hint={{ day: refunding.requested_on, amount: refundLeft(refunding), currency: refunding.currency }}
      onClose={() => setRefunding(null)} onDone={(text) => { setRefunding(null); onChanged(text); }} />}
    {removing && <ConfirmDialog title={t('pur.ret.deleteTitle')} confirmLabel={t('pur.delete')} danger busy={busy} onConfirm={() => void remove()} onCancel={() => setRemoving(null)}>
      <p>{t('pur.ret.deleteText')}</p></ConfirmDialog>}
  </section>;
}

function ReturnForm({ purchase, item, onClose, onSaved }: { purchase: PurchaseDetail; item: PurchaseReturn | null; onClose: () => void; onSaved: () => void }) {
  const chosen = new Map((item?.items ?? []).map((l) => [l.item_id, String(Number(l.quantity))]));
  const [picked, setPicked] = useState<Record<string, string>>(() => Object.fromEntries(purchase.items.map((i) => [i.id, chosen.get(i.id) ?? ''])));
  const [f, setF] = useState({ reason: item?.reason ?? 'defective', reason_note: item?.reason_note ?? '', amount: item?.amount ?? '', deadline_on: item?.deadline_on ?? purchase.return_window_until ?? '',
    pickup_on: item?.pickup_on ?? '', courier: item?.courier ?? '', tracking_number: item?.tracking_number ?? '', proof_document_id: item?.proof_document_id ?? '',
    confirmation_ref: item?.confirmation_ref ?? '', refund_ref: item?.refund_ref ?? '', expected_refund_on: item?.expected_refund_on ?? '', dispute_note: item?.dispute_note ?? '' });
  const [key] = useState(() => newKey('return'));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (name: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [name]: e.target.value });
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const lines = [];
    for (const i of purchase.items) {
      const raw = picked[i.id];
      if (!raw) continue;
      const quantity = decimal(raw, 3);
      if (!quantity || Number(quantity) <= 0 || Number(quantity) > Number(i.quantity)) return setError(t('pur.ret.badQuantity', { item: i.description }));
      lines.push({ item_id: i.id, quantity });
    }
    const amount = f.amount ? decimal(f.amount) : null;
    if (f.amount && !amount) return setError(t('pur.form.badNumber'));
    const body = { reason: f.reason as PurchaseReturnReason, reason_note: f.reason_note.trim() || null, items: lines, amount, deadline_on: f.deadline_on || null,
      pickup_on: f.pickup_on || null, courier: f.courier.trim() || null, tracking_number: f.tracking_number.trim() || null, proof_document_id: f.proof_document_id.trim() || null,
      confirmation_ref: f.confirmation_ref.trim() || null, expected_refund_on: f.expected_refund_on || null };
    setBusy(true); setError(null);
    try {
      if (item) await api.updateReturn(item.id, { ...body, version: item.version, refund_ref: f.refund_ref.trim() || null, dispute_note: f.dispute_note.trim() || null });
      else await api.createReturn(purchase.id, body, key);
      onSaved();
    } catch (err) { setError(describeError(err)); } finally { setBusy(false); }
  };
  return <Modal title={item ? t('pur.ret.editTitle') : t('pur.ret.addTitle')} className="purDialog" onClose={onClose}>
    <form className="purForm" noValidate onSubmit={(e) => void submit(e)}>
      {purchase.items.length > 0 && <fieldset className="purFieldset"><legend>{t('pur.ret.which')}</legend>
        <p className="muted purNote">{t('pur.ret.whichHint')}</p>
        {purchase.items.map((i) => <label key={i.id} className="field purPick"><span dir="auto">{t('pur.ret.howMany', { item: i.description, max: Number(i.quantity) })}</span>
          <input inputMode="decimal" value={picked[i.id] ?? ''} onChange={(e) => setPicked({ ...picked, [i.id]: e.target.value })} /></label>)}
      </fieldset>}
      <div className="purTwo">
        <label className="field"><span>{t('pur.ret.reason')}</span><select value={f.reason} onChange={set('reason')}>{PURCHASE_RETURN_REASONS.map((r) => <option key={r} value={r}>{reasonLabel(r)}</option>)}</select></label>
        <label className="field"><span>{t('pur.ret.amountField', { currency: purchase.currency })}</span><input inputMode="decimal" value={f.amount} onChange={set('amount')} /></label>
        <label className="field"><span>{t('pur.ret.deadline')}</span><input type="date" value={f.deadline_on} onChange={set('deadline_on')} /></label>
        <label className="field"><span>{t('pur.ret.pickupOn')}</span><input type="date" value={f.pickup_on} onChange={set('pickup_on')} /></label>
        <label className="field"><span>{t('pur.ret.courier')}</span><input dir="auto" maxLength={60} value={f.courier} onChange={set('courier')} /></label>
        <label className="field"><span>{t('pur.ship.tracking')}</span><input dir="ltr" maxLength={60} value={f.tracking_number} onChange={set('tracking_number')} /></label>
        <label className="field"><span>{t('pur.ret.confirmation')}</span><input dir="ltr" maxLength={80} value={f.confirmation_ref} onChange={set('confirmation_ref')} /></label>
        <label className="field"><span>{t('pur.ret.expectedRefund')}</span><input type="date" value={f.expected_refund_on} onChange={set('expected_refund_on')} /></label>
        <label className="field"><span>{t('pur.ret.proofField')}</span><input dir="ltr" maxLength={80} placeholder="doc_..." value={f.proof_document_id} onChange={set('proof_document_id')} /></label>
        {item && <label className="field"><span>{t('pur.ret.refundRef')}</span><input dir="ltr" maxLength={80} value={f.refund_ref} onChange={set('refund_ref')} /></label>}
      </div>
      <label className="field"><span>{t('pur.ret.note')}</span><input dir="auto" maxLength={300} value={f.reason_note} onChange={set('reason_note')} /></label>
      {item && <label className="field"><span>{t('pur.ret.dispute')}</span><textarea dir="auto" maxLength={500} rows={2} value={f.dispute_note} onChange={set('dispute_note')} /></label>}
      <FormError id="pur-ret-error" text={error} />
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('pur.saving') : t('pur.save')}</button><button type="button" className="secondary" disabled={busy} onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ---- The payment and refund picker ----------------------------------------------------------------------

export function MoneyDialog({ mode, targetId, current, hint, onClose, onDone }: {
  mode: 'payment' | 'refund'; targetId: string; current: PurchaseMoneyLink | null; hint: { day: string | null; amount: string | null; currency: string };
  onClose: () => void; onDone: (text: string) => void;
}) {
  const [how, setHow] = useState<'link' | 'cash'>('link');
  const [q, setQ] = useState('');
  const [list, setList] = useState<Load<PurchasePaymentCandidate[]>>({ kind: 'loading' });
  const [accounts, setAccounts] = useState<Load<FinanceAccount[]>>({ kind: 'loading' });
  const [cash, setCash] = useState({ account_id: '', amount: hint.amount ?? '', posted_on: mode === 'refund' ? today() : (hint.day ?? today()) });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [key] = useState(() => newKey(mode));
  const search = useCallback(async (text: string) => {
    setList({ kind: 'loading' });
    // A ranking hint only: nothing left (0.00) or no figure is not sent, the server takes only amounts above zero.
    const amount = hint.amount && Number(hint.amount) > 0 ? hint.amount : null;
    try { setList({ kind: 'ready', value: (await api.candidates(mode, text, hint.day, amount)).items }); } catch (e) { setList(failed(e)); }
  }, [mode, hint.day, hint.amount]);
  const linked = current && current.status !== 'void';
  useEffect(() => { if (!linked) void search(''); }, [search, linked]);
  useEffect(() => {
    if (how !== 'cash' || accounts.kind === 'ready') return;
    void api.financeAccounts().then((rows) => { setAccounts({ kind: 'ready', value: rows }); setCash((c) => ({ ...c, account_id: c.account_id || (rows.find((a) => a.kind === 'cash') ?? rows[0])?.id || '' })); },
      (e) => setAccounts(failed(e)));
  }, [how, accounts.kind]);
  const act = async (work: () => Promise<unknown>, text: string) => {
    setBusy(true); setError(null);
    try { await work(); onDone(text); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  const prefix = mode === 'payment' ? 'pur.pay' : 'pur.refund';
  return <Modal title={t(`${prefix}.title` as MessageKey)} className="purDialog" onClose={onClose}>
    <p className="muted purNote">{t(`${prefix}.rule` as MessageKey)}</p>
    {linked && <div className="purPayNow">
      <p><strong>{linkText(current)}</strong>{current!.recorded_as_cash ? ` · ${t('pur.pay.cashRecorded')}` : ''}</p>
      {current!.status === 'needs_review' && <Notice>{t('pur.pay.reviewText')}</Notice>}
      {current!.link_id && <button type="button" className="secondary danger" disabled={busy} onClick={() => void act(() => api.unlinkPayment(targetId), t('pur.pay.unlinked'))}><Unlink aria-hidden="true" />{t('pur.pay.unlink')}</button>}
      <p className="muted purNote">{t('pur.pay.unlinkNote')}</p>
    </div>}
    {!linked && <>
      {current?.status === 'void' && <Notice>{linkText(current)}</Notice>}
      <div className="cmSegment" role="radiogroup" aria-label={t('pur.pay.how')}>
        <button type="button" role="radio" aria-checked={how === 'link'} onClick={() => setHow('link')}>{t(`${prefix}.modeLink` as MessageKey)}</button>
        <button type="button" role="radio" aria-checked={how === 'cash'} onClick={() => setHow('cash')}>{t(`${prefix}.modeCash` as MessageKey)}</button>
      </div>
      {how === 'link' && <>
        <form className="cmFilters" role="search" onSubmit={(e) => { e.preventDefault(); void search(q.trim()); }}>
          <label className="cmInline cmSearch"><span>{t('pur.pay.search')}</span><input type="search" dir="auto" value={q} maxLength={80} onChange={(e) => setQ(e.target.value)} /></label>
          <button type="submit" className="secondary">{t('pur.list.find')}</button>
        </form>
        {list.kind === 'loading' && <Notice busy>{t('pur.pay.loading')}</Notice>}
        {list.kind === 'denied' && <Notice>{t('pur.pay.noFinance')}</Notice>}
        {list.kind === 'error' && <LoadError message={t('pur.error.payments', { message: list.message })} retry={() => void search(q.trim())} />}
        {list.kind === 'ready' && list.value.length === 0 && <p className="muted">{t(`${prefix}.noneFound` as MessageKey)}</p>}
        {list.kind === 'ready' && list.value.length > 0 && <ul className="cmList purPayList">{list.value.map((c) => <li key={`${c.canonical_id}:${c.currency}`}>
          <div><strong dir="auto">{c.merchant}</strong><small>{c.posted_on} · <span dir="ltr">{formatAmount(c.amount, c.currency)}</span>
            {c.remaining !== c.amount ? ` · ${t('pur.pay.left', { amount: formatAmount(c.remaining, c.currency) })}` : ''}</small></div>
          <button type="button" className="secondary" disabled={busy || Number(c.remaining) <= 0} aria-label={t('pur.pay.chooseLabel', { what: `${c.merchant} ${c.posted_on}` })}
            onClick={() => void act(() => (mode === 'payment' ? api.linkPayment(targetId, { canonical_id: c.canonical_id, currency: c.currency }, key)
              : api.linkRefund(targetId, { canonical_id: c.canonical_id, currency: c.currency }, key)), t(`${prefix}.linked` as MessageKey))}>{t('pur.pay.choose')}</button>
        </li>)}</ul>}
      </>}
      {how === 'cash' && <form className="purForm" noValidate onSubmit={(e) => { e.preventDefault(); const amount = decimal(cash.amount);
        if (!amount || Number(amount) <= 0 || !cash.account_id) { setError(t('pur.pay.cashNeeds')); return; }
        const body = { account_id: cash.account_id, amount, posted_on: cash.posted_on };
        void act(() => (mode === 'payment' ? api.recordCash(targetId, body, key) : api.recordCashRefund(targetId, body, key)), t(`${prefix}.cashDone` as MessageKey)); }}>
        <p className="muted purNote">{t(`${prefix}.cashRule` as MessageKey)}</p>
        {accounts.kind === 'loading' && <Notice busy>{t('pur.loading')}</Notice>}
        {(accounts.kind === 'error' || accounts.kind === 'denied') && <Notice error>{accounts.kind === 'denied' ? t('pur.pay.noFinance') : accounts.message}</Notice>}
        {accounts.kind === 'ready' && accounts.value.length === 0 && <p className="muted">{t('pur.pay.noAccounts')} <a href="#finance">{t('pur.pay.openFinance')}</a></p>}
        {accounts.kind === 'ready' && accounts.value.length > 0 && <div className="purTwo">
          <label className="field"><span>{t('pur.pay.account')}</span><select value={cash.account_id} onChange={(e) => setCash({ ...cash, account_id: e.target.value })}>
            {accounts.value.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.currency})</option>)}</select></label>
          <label className="field"><span>{t('pur.pay.amount')}</span><input inputMode="decimal" value={cash.amount} onChange={(e) => setCash({ ...cash, amount: e.target.value })} /></label>
          <label className="field"><span>{t('pur.pay.date')}</span><input type="date" max={today()} value={cash.posted_on} onChange={(e) => setCash({ ...cash, posted_on: e.target.value })} /></label>
        </div>}
        <div className="actions"><button type="submit" disabled={busy || accounts.kind !== 'ready' || accounts.value.length === 0}>{busy ? t('pur.saving') : t('pur.pay.cashSave')}</button></div>
      </form>}
    </>}
    <FormError id="pur-pay-error" text={error} />
    <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('dialog.close')}</button></div>
  </Modal>;
}

// ---- History -------------------------------------------------------------------------------------------

export function eventText(event: PurchaseEvent): string {
  if (event.kind === 'status' && event.to_status) {
    return event.from_status ? t('pur.event.moved', { from: statusLabel(event.from_status), to: statusLabel(event.to_status) }) : t('pur.event.created', { status: statusLabel(event.to_status) });
  }
  return t(`pur.ekind.${event.kind}` as MessageKey);
}

function ActivityPanel({ purchase, onChanged }: { purchase: PurchaseDetail; onChanged: (text?: string) => void }) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Load<PurchasePage<PurchaseEvent>>>({ kind: 'loading' });
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);
  const load = useCallback(async () => {
    try { setPage({ kind: 'ready', value: await api.listEvents(purchase.id, offset, PAGE_SIZE) }); } catch (e) { setPage(failed(e)); }
  }, [purchase.id, offset]);
  useEffect(() => { void load(); }, [load]);
  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (!note.trim()) return setMessage({ text: t('pur.event.needNote'), error: true });
    setBusy(true); setMessage(null);
    try { await api.addNote(purchase.id, note.trim()); setNote(''); setOffset(0); void load(); onChanged(); } catch (err) { setMessage({ text: describeError(err), error: true }); } finally { setBusy(false); }
  };
  return <section className="surface cmPanel" aria-labelledby="pur-act-title">
    <h2 id="pur-act-title">{t('pur.event.title')}</h2>
    <p className="muted purNote">{t('pur.event.rule')}</p>
    <form className="purForm purMove" noValidate onSubmit={(e) => void add(e)}>
      <label className="field"><span>{t('pur.event.note')}</span><input dir="auto" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} /></label>
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('pur.saving') : t('pur.event.add')}</button></div>
    </form>
    {message && <Notice error={message.error}>{message.text}</Notice>}
    {page.kind === 'loading' && <Notice busy>{t('pur.loading')}</Notice>}
    {(page.kind === 'error' || page.kind === 'denied') && <LoadError message={t('pur.error.load', { message: page.message })} retry={() => void load()} />}
    {page.kind === 'ready' && page.value.total === 0 && <p className="muted">{t('pur.event.none')}</p>}
    {page.kind === 'ready' && page.value.total > 0 && <>
      <ol className="cmList purHistory">{page.value.items.map((e) => <li key={e.id}><div>
        <strong>{eventText(e)}</strong>
        <small>{[formatDateTime(e.occurred_at), t(`pur.source.${e.source}` as MessageKey), e.note ?? ''].filter(Boolean).join(' · ')}</small></div></li>)}</ol>
      <Pager total={page.value.total} offset={offset} onChange={setOffset} />
    </>}
  </section>;
}

// ---- Details: edit, payment, archive, delete ------------------------------------------------------------

function DetailsPanel({ purchase, onChanged, onDeleted }: { purchase: PurchaseDetail; onChanged: (text?: string) => void; onDeleted: () => void }) {
  const [editing, setEditing] = useState(false);
  const [paying, setPaying] = useState(false);
  const [confirm, setConfirm] = useState<'archive' | 'delete' | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);
  const run = async (work: () => Promise<unknown>, after: () => void) => {
    setBusy(true); setMessage(null);
    try { await work(); setConfirm(null); after(); } catch (e) { setConfirm(null); setMessage({ text: describeError(e), error: true }); } finally { setBusy(false); }
  };
  const row = (label: MessageKey, value: ReactNode) => <><dt>{t(label)}</dt><dd>{value || '-'}</dd></>;
  return <div className="purGrid">
    <section className="surface cmPanel" aria-labelledby="pur-facts-title">
      <div className="purBar"><h2 id="pur-facts-title">{t('pur.details.title')}</h2>
        {purchase.can_edit && <button type="button" className="secondary" onClick={() => setEditing(true)}><Pencil aria-hidden="true" />{t('pur.edit')}</button>}</div>
      <dl className="purFacts">
        {row('pur.form.merchant', <bdi>{purchase.merchant}</bdi>)}
        {row('pur.form.orderRef', purchase.order_ref ? <span dir="ltr">{purchase.order_ref}</span> : null)}
        {row('pur.form.orderedOn', purchase.ordered_on)}
        {row('pur.form.currency', purchase.currency)}
        {row('pur.form.total', purchase.total ? formatAmount(purchase.total, purchase.currency) : null)}
        {row('pur.form.tax', purchase.tax_amount ? formatAmount(purchase.tax_amount, purchase.currency) : null)}
        {row('pur.form.shipping', purchase.shipping_amount ? formatAmount(purchase.shipping_amount, purchase.currency) : null)}
        {row('pur.form.method', purchase.payment_method ? methodLabel(purchase.payment_method) : null)}
        {row('pur.form.returnWindow', purchase.return_window_until)}
        {row('pur.form.warranty', purchase.warranty_until)}
        {row('pur.form.address', purchase.address_ref)}
        {row('pur.form.document', purchase.document_id ? <a href="#vault" dir="ltr">{purchase.document_id}</a> : null)}
        {row('pur.form.notes', purchase.notes)}
      </dl>
      {!purchase.can_edit && <p className="muted purNote">{t('pur.details.readOnly')}</p>}
    </section>
    <section className="surface cmPanel" aria-labelledby="pur-actions-title">
      <h2 id="pur-actions-title">{t('pur.details.actions')}</h2>
      <p>{t('pur.details.payment')}: <span className={`purChip ${linkState(purchase.payment) === 'active' ? 'done' : linkState(purchase.payment) === 'needs_review' ? 'warn' : 'closed'}`}>{linkText(purchase.payment)}</span></p>
      {!purchase.mine && <p className="muted purNote">{t('pur.details.payOwner')}</p>}
      <div className="purRowButtons">
        {purchase.can_pay && <button type="button" className="secondary" onClick={() => setPaying(true)}><Link2 aria-hidden="true" />{t('pur.details.pay')}</button>}
        {purchase.can_edit && <button type="button" className="secondary" onClick={() => setConfirm('archive')}>{purchase.archived_at ? <ArchiveRestore aria-hidden="true" /> : <Archive aria-hidden="true" />}{purchase.archived_at ? t('pur.details.restore') : t('pur.details.archive')}</button>}
        {purchase.can_edit && <button type="button" className="secondary danger" onClick={() => setConfirm('delete')}><Trash2 aria-hidden="true" />{t('pur.details.delete')}</button>}
      </div>
      {message && <Notice error={message.error}>{message.text}</Notice>}
    </section>
    {editing && <PurchaseForm purchase={purchase} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); onChanged(t('pur.saved')); }} />}
    {paying && <MoneyDialog mode="payment" targetId={purchase.id} current={purchase.payment} hint={{ day: purchase.ordered_on, amount: purchase.total ?? purchase.items_total ?? null, currency: purchase.currency }}
      onClose={() => setPaying(false)} onDone={(text) => { setPaying(false); onChanged(text); }} />}
    {confirm === 'archive' && <ConfirmDialog title={purchase.archived_at ? t('pur.details.restore') : t('pur.details.archive')} confirmLabel={purchase.archived_at ? t('pur.details.restore') : t('pur.details.archive')} busy={busy}
      onConfirm={() => void run(() => api.archivePurchase(purchase.id, purchase.version, !purchase.archived_at), () => onChanged())} onCancel={() => setConfirm(null)}>
      <p>{purchase.archived_at ? t('pur.details.restoreText') : t('pur.details.archiveText')}</p></ConfirmDialog>}
    {confirm === 'delete' && <ConfirmDialog title={t('pur.details.delete')} confirmLabel={t('pur.details.delete')} danger busy={busy}
      onConfirm={() => void run(() => api.deletePurchase(purchase.id), onDeleted)} onCancel={() => setConfirm(null)}>
      <p>{t('pur.details.deleteText', { name: purchaseName(purchase) })}</p></ConfirmDialog>}
  </div>;
}
