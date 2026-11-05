// The purchase tracker (docs/purchases.md#web), on the shared shell (FinanceShell.tsx): the filter bar, the tracker table,
// the delivery and refund task queue, Import order and the order drawer. Every figure and row comes from the API; tracking
// shows the number and status the person typed or shared (there is no carrier integration), with the carrier's own public
// page as a plain link where the carrier is known. A refund lowers what a purchase cost; it is never income.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import {
  Camera, Check, CircleAlert, Copy, ExternalLink, FileText, History, Link2, MoreHorizontal, PackageOpen, Pencil, Receipt, RotateCcw, Search, Truck,
  Undo2, Upload, X,
} from 'lucide-react';
import type { Purchase, PurchaseDetail, PurchaseDueItem, PurchasePage, PurchaseReturn, PurchaseShipment, VaultDocument } from '@anum/contracts';
import * as api from './lib/purchasesApi';
import * as vaultApi from './lib/vaultApi';
import {
  DATE_RANGES, FILTERS, PAGE_SIZE, PAYMENT_FILTERS, SOURCE_FILTERS, NO_FILTERS, carrierKey, carrierUrl, chipTone, currentReturn, dueLabel, dueWhen,
  filterLabel, filtersActive, linkState, maskTracking, methodLabel, newKey, purchaseName, rangeStart, reasonLabel, refundLeft, refundMatch, returnStatusLabel,
  rowTitle, shipmentLabel, statusLabel, taskAction, today, trackerSteps, type DateRange, type TrackerFilters,
} from './lib/purchases';
import { documentHash } from './lib/vault';
import { formatAmount } from './lib/finance';
import { copyText } from './lib/clipboard';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Empty, FormError, LoadError, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, StatusChip } from './FinanceShell';
import { MoneyDialog, PurchaseForm, ShipmentForm, eventText } from './PurchaseParts';

export const shortDay = (value: string | null | undefined) => (value ? formatDateTime(`${value}T00:00:00Z`, { day: 'numeric', month: 'short', timeZone: 'UTC' }) : '-');
const stamp = (value: string) => formatDateTime(value, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

// ---- Filters -------------------------------------------------------------------------------------------

export function TrackerFilterBar({ value, onChange, statusChoices = FILTERS, label }: {
  value: TrackerFilters; onChange: (next: TrackerFilters) => void; statusChoices?: readonly string[]; label: string;
}) {
  const [q, setQ] = useState(value.q);
  const [sellers, setSellers] = useState<string[]>([]);
  const ids = { q: useId(), seller: useId(), status: useId(), range: useId(), payment: useId(), source: useId() };
  useEffect(() => { setQ(value.q); }, [value.q]);
  useEffect(() => { api.merchants().then((r) => setSellers(r.items), () => setSellers([])); }, []);
  const set = (patch: Partial<TrackerFilters>) => onChange({ ...value, ...patch });
  return <form className="purFilterBar" role="search" aria-label={label} onSubmit={(e) => { e.preventDefault(); set({ q: q.trim() }); }}>
    <label className="purSearch" htmlFor={ids.q}><Search aria-hidden="true" /><span className="visuallyHidden">{t('pur.filter.search')}</span>
      <input id={ids.q} type="search" dir="auto" maxLength={80} value={q} placeholder={t('pur.filter.searchHint')} onChange={(e) => setQ(e.target.value)}
        onBlur={() => { if (q.trim() !== value.q) set({ q: q.trim() }); }} /></label>
    <label className="purSelect" htmlFor={ids.seller}><span>{t('pur.filter.seller')}</span>
      <select id={ids.seller} value={value.merchant} onChange={(e) => set({ merchant: e.target.value })}>
        <option value="">{t('pur.filter.allShort')}</option>
        {value.merchant && !sellers.includes(value.merchant) && <option value={value.merchant}>{value.merchant}</option>}
        {sellers.map((name) => <option key={name} value={name}>{name}</option>)}
      </select></label>
    <label className="purSelect" htmlFor={ids.status}><span>{t('pur.filter.status')}</span>
      <select id={ids.status} value={value.status} onChange={(e) => set({ status: e.target.value })}>
        {statusChoices.map((f) => <option key={f} value={f}>{f ? filterLabel(f) : t('pur.filter.allShort')}</option>)}
      </select></label>
    <label className="purSelect" htmlFor={ids.range}><span>{t('pur.filter.date')}</span>
      <select id={ids.range} value={value.range} onChange={(e) => set({ range: e.target.value as DateRange })}>
        {DATE_RANGES.map((r) => <option key={r} value={r}>{t(`pur.range.${r || 'all'}` as MessageKey)}</option>)}
      </select></label>
    <label className="purSelect" htmlFor={ids.payment}><span>{t('pur.filter.payment')}</span>
      <select id={ids.payment} value={value.payment} onChange={(e) => set({ payment: e.target.value })}>
        {PAYMENT_FILTERS.map((p) => <option key={p} value={p}>{p === '' ? t('pur.filter.allShort') : p === 'linked' || p === 'unlinked' ? t(`pur.filter.pay.${p}` as MessageKey) : methodLabel(p)}</option>)}
      </select></label>
    <label className="purSelect" htmlFor={ids.source}><span>{t('pur.filter.source')}</span>
      <select id={ids.source} value={value.source} onChange={(e) => set({ source: e.target.value })}>
        {SOURCE_FILTERS.map((s) => <option key={s} value={s}>{s ? t(`pur.source.${s}` as MessageKey) : t('pur.filter.allShort')}</option>)}
      </select></label>
    {filtersActive(value) && <button type="button" className="secondary purClear" onClick={() => { setQ(''); onChange(NO_FILTERS); }}><RotateCcw aria-hidden="true" />{t('pur.filter.clear')}</button>}
  </form>;
}

export function listQuery(filters: TrackerFilters, extra: { status?: string; warranty?: boolean; archived?: boolean } = {}) {
  return {
    q: filters.q, merchant: filters.merchant, status: (extra.status ?? filters.status) as never, from: rangeStart(filters.range), to: filters.range ? today() : null,
    payment: filters.payment as never, source: filters.source as never, warranty: extra.warranty, archived: extra.archived,
  };
}

/** One page of the tracker's purchases for the filters, with its own loading, error and permission states. */
export function usePurchasePage(filters: TrackerFilters, extra: { status?: string; warranty?: boolean; archived?: boolean }, offset: number, limit: number, tick: number) {
  const [page, setPage] = useState<Load<PurchasePage<Purchase>>>({ kind: 'loading' });
  const key = JSON.stringify([filters, extra, offset, limit, tick]);
  const run = useRef(0);
  const load = useCallback(async () => {
    const token = ++run.current;
    setPage((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    try {
      const value = await api.findPurchases(listQuery(filters, extra), offset, limit);
      if (token === run.current) setPage({ kind: 'ready', value });
    } catch (e) { if (token === run.current) setPage(failed(e)); }
  }, [key]);
  useEffect(() => { void load(); }, [load]);
  return { page, reload: load };
}

// ---- The tracker table ------------------------------------------------------------------------------------

function PaymentCell({ p }: { p: Purchase }) {
  const state = linkState(p.payment);
  return <span className="purCell2"><span>{methodLabel(p.payment_method)}</span>
    <small className={state === 'active' ? 'purOk' : state === 'needs_review' ? 'purWarn' : 'muted'}>
      {state === 'active' ? t('pur.pay.linked') : state === 'needs_review' ? t('pur.pay.reviewShort') : t('pur.pay.notLinked')}</small></span>;
}

function DeliveryCell({ p }: { p: Purchase }) {
  if (p.delivered_on) return <span className="purCell2"><span className="purOk"><Check aria-hidden="true" />{t('pur.delivery.on', { day: shortDay(p.delivered_on) })}</span></span>;
  const ship = p.latest_shipment;
  if (ship && ship.status !== 'pending') return <span className="purCell2"><span><Truck aria-hidden="true" />{shipmentLabel(ship.status)}</span>
    {(ship.expected_on || p.expected_delivery_on) && <small className="muted">{t('pur.delivery.est', { day: shortDay(ship.expected_on ?? p.expected_delivery_on) })}</small>}</span>;
  return <span>{p.expected_delivery_on ? shortDay(p.expected_delivery_on) : '-'}</span>;
}

function ReturnDeadline({ p }: { p: Purchase }) {
  const window = p.due.find((d) => d.kind === 'return_window');
  if (window) return <span className="purCell2"><span className="purWarn">{t('pur.deadline.closes')}</span><small>{shortDay(window.due_on)}</small></span>;
  if (p.return_window_until && p.return_window_until >= today()) return <span className="purCell2"><span>{t('pur.deadline.until')}</span><small>{shortDay(p.return_window_until)}</small></span>;
  return <span aria-label={t('pur.deadline.none')}>-</span>;
}

export function TrackerTable({ page, selected, onSelect, onOpenFull, caption, columns = 'tracker' }: {
  page: PurchasePage<Purchase>; selected: string | null; onSelect: (id: string) => void; onOpenFull: (id: string) => void; caption: string;
  columns?: 'tracker' | 'deliveries' | 'warranty';
}) {
  const [menu, setMenu] = useState<string | null>(null);
  return <div className="finTableWrap">
    <table className="finGrid purTracker">
      <caption className="visuallyHidden">{caption}</caption>
      <thead><tr>
        <th scope="col">{t('pur.col.item')}</th>
        <th scope="col">{t('pur.col.orderDate')}</th>
        {columns === 'tracker' && <><th scope="col">{t('pur.col.expected')}</th><th scope="col">{t('pur.col.payment')}</th><th scope="col">{t('pur.col.status')}</th>
          <th scope="col">{t('pur.col.deadline')}</th></>}
        {columns === 'deliveries' && <><th scope="col">{t('pur.col.carrier')}</th><th scope="col">{t('pur.col.tracking')}</th><th scope="col">{t('pur.col.shipment')}</th>
          <th scope="col">{t('pur.col.expected')}</th></>}
        {columns === 'warranty' && <><th scope="col">{t('pur.col.warrantyEnd')}</th><th scope="col">{t('pur.col.status')}</th></>}
        <th scope="col" className="num">{t('pur.col.total')}</th>
        <th scope="col"><span className="visuallyHidden">{t('pur.col.actions')}</span></th>
      </tr></thead>
      <tbody>{page.items.map((p) => {
        const name = rowTitle(p);
        return <tr key={p.id} className={selected === p.id ? 'selected' : undefined} aria-selected={selected === p.id || undefined}>
          <th scope="row"><button type="button" className="purRowButton" onClick={() => onSelect(p.id)} aria-label={t('pur.openLabel', { name: `${name}, ${p.merchant}` })}>
            <strong dir="auto"><bdi>{name}</bdi></strong><small dir="auto"><bdi>{p.merchant}</bdi>{p.order_ref ? <> · <bdi dir="ltr">{p.order_ref}</bdi></> : null}</small>
          </button>{p.needs_review && <StatusChip tone="warn">{t('pur.review.chip')}</StatusChip>}</th>
          <td>{shortDay(p.ordered_on)}</td>
          {columns === 'tracker' && <>
            <td><DeliveryCell p={p} /></td>
            <td><PaymentCell p={p} /></td>
            <td><span className="purCell2"><StatusChip tone={chipTone(p.status)}>{statusLabel(p.status)}</StatusChip>
              {p.refunded.length > 0 && <small className="muted">{t('pur.row.refunded', { amount: p.refunded.map((r) => formatAmount(r.amount, r.currency)).join(', ') })}</small>}</span></td>
            <td><ReturnDeadline p={p} /></td>
          </>}
          {columns === 'deliveries' && <>
            <td dir="auto">{p.latest_shipment?.carrier ?? '-'}</td>
            <td><bdi dir="ltr" className="purMono">{maskTracking(p.latest_shipment?.tracking_number)}</bdi></td>
            <td>{p.latest_shipment ? <StatusChip tone={p.latest_shipment.status === 'delivered' ? 'ok' : p.latest_shipment.status === 'failed' ? 'bad' : 'info'}>{shipmentLabel(p.latest_shipment.status)}</StatusChip>
              : <StatusChip tone="muted">{t('pur.ship.noneShort')}</StatusChip>}</td>
            <td>{shortDay(p.latest_shipment?.expected_on ?? p.expected_delivery_on)}</td>
          </>}
          {columns === 'warranty' && <>
            <td>{p.warranty_until ? shortDay(p.warranty_until) : <span className="muted">{t('pur.warranty.onItems')}</span>}</td>
            <td><StatusChip tone={chipTone(p.status)}>{statusLabel(p.status)}</StatusChip></td>
          </>}
          <td className="num" dir="ltr">{p.total ? formatAmount(p.total, p.currency) : '-'}</td>
          <td className="purMenuCell">
            <button type="button" className="purIcon" aria-haspopup="menu" aria-expanded={menu === p.id} aria-label={t('pur.row.menu', { name })}
              onClick={() => setMenu(menu === p.id ? null : p.id)}><MoreHorizontal aria-hidden="true" /></button>
            {menu === p.id && <div className="purMenu" role="menu" onKeyDown={(e) => { if (e.key === 'Escape') setMenu(null); }}>
              <button type="button" role="menuitem" autoFocus onClick={() => { setMenu(null); onSelect(p.id); }}>{t('pur.row.details')}</button>
              <button type="button" role="menuitem" onClick={() => { setMenu(null); onOpenFull(p.id); }}>{t('pur.row.fullRecord')}</button>
            </div>}
          </td>
        </tr>;
      })}</tbody>
    </table>
  </div>;
}

export function PagerBar({ total, offset, limit, onChange }: { total: number; offset: number; limit: number; onChange: (offset: number) => void }) {
  if (total <= limit) return null;
  return <div className="purPager">
    <span className="muted">{t('pur.page.showing', { from: offset + 1, to: Math.min(offset + limit, total), total })}</span>
    <button type="button" className="secondary" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>{t('pur.page.previous')}</button>
    <button type="button" className="secondary" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>{t('pur.page.next')}</button>
  </div>;
}

/** The tracker card: loading, error, empty (with the next action) or the table with paging. */
export function TrackerCard({ title, filters, extra = {}, limit = PAGE_SIZE, selected, onSelect, onOpenFull, onAdd, tick, actions, columns, emptyTitle, emptyText, onDenied }: {
  title: string; filters: TrackerFilters; extra?: { status?: string; warranty?: boolean; archived?: boolean }; limit?: number; selected: string | null;
  onSelect: (id: string) => void; onOpenFull: (id: string) => void; onAdd?: () => void; tick: number; actions?: ReactNode; columns?: 'tracker' | 'deliveries' | 'warranty';
  emptyTitle?: string; emptyText?: string; onDenied: () => void;
}) {
  const [offset, setOffset] = useState(0);
  useEffect(() => { setOffset(0); }, [filters, extra.status, extra.warranty, extra.archived]);
  const { page, reload } = usePurchasePage(filters, extra, offset, limit, tick);
  useEffect(() => { if (page.kind === 'denied') onDenied(); }, [page.kind, onDenied]);
  const count = page.kind === 'ready' ? t('pur.tracker.count', { count: page.value.total }) : undefined;
  return <FinCard title={title} subtitle={count} actions={actions} className="purTrackerCard">
    {page.kind === 'loading' && <Notice busy>{t('pur.loading')}</Notice>}
    {page.kind === 'error' && <LoadError message={t('pur.error.load', { message: page.message })} retry={() => void reload()} />}
    {page.kind === 'ready' && page.value.total === 0 && (filtersActive(filters)
      ? <Empty icon={<Search aria-hidden="true" />} title={t('pur.list.noMatchTitle')} text={t('pur.list.noMatchText')} />
      : <Empty icon={<PackageOpen aria-hidden="true" />} title={emptyTitle ?? t('pur.empty.title')} text={emptyText ?? t('pur.empty.text')}
        action={onAdd ? <button type="button" onClick={onAdd}>{t('pur.track')}</button> : undefined} />)}
    {page.kind === 'ready' && page.value.total > 0 && <>
      <TrackerTable page={page.value} selected={selected} onSelect={onSelect} onOpenFull={onOpenFull} caption={title} columns={columns} />
      <PagerBar total={page.value.total} offset={offset} limit={limit} onChange={setOffset} />
    </>}
  </FinCard>;
}

// ---- Delivery and refund tasks --------------------------------------------------------------------------

const TASK_ICONS: Record<string, ReactNode> = {
  shipment: <Truck aria-hidden="true" />, proof: <Camera aria-hidden="true" />, return: <Undo2 aria-hidden="true" />, refund: <Receipt aria-hidden="true" />,
  open: <CircleAlert aria-hidden="true" />,
};

export function TaskQueue({ due, onOpen }: { due: PurchaseDueItem[]; onOpen: (purchaseId: string) => void }) {
  const tasks = due.filter((d) => d.kind !== 'warranty' || d.days_left <= 7).slice(0, 8);
  return <FinCard title={t('pur.tasks.title')} subtitle={t('pur.tasks.subtitle')} className="purTasks">
    {tasks.length === 0 ? <p className="muted">{t('pur.tasks.none')}</p>
      : <ul className="purTaskList">{tasks.map((item) => {
        const action = taskAction(item.kind);
        const overdue = item.days_left <= 0;
        return <li key={`${item.purchase_id}:${item.kind}:${item.source_id}`}>
          <span className="purTaskIcon">{TASK_ICONS[action]}</span>
          <StatusChip tone={overdue ? 'bad' : 'warn'}>{dueLabel(item)}</StatusChip>
          <span className="purTaskText"><strong dir="auto"><bdi>{purchaseName(item)}</bdi></strong><small>{t(`pur.task.text.${item.kind}` as MessageKey)}</small></span>
          <span className={`purTaskDue ${overdue ? 'purBad' : ''}`}>{dueWhen(item, shortDay)}</span>
          <button type="button" className="secondary" aria-label={`${t(`pur.task.action.${action}` as MessageKey)}: ${purchaseName(item)}`}
            onClick={() => onOpen(item.purchase_id)}>{t(`pur.task.action.${action}` as MessageKey)}</button>
        </li>;
      })}</ul>}
  </FinCard>;
}

// ---- Import order ----------------------------------------------------------------------------------------

export function ImportOrderDialog({ onClose, onImported }: { onClose: () => void; onImported: (purchaseId: string | null, message: string) => void }) {
  const [text, setText] = useState('');
  const [ownership, setOwnership] = useState<'personal' | 'business'>('personal');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [key] = useState(() => newKey('import'));
  const id = useId();
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) { setError(t('pur.import.empty')); return; }
    setBusy(true); setError(null);
    try {
      const result = await api.importOrder(text.trim(), key, ownership);
      onImported(result.purchase_id ?? null, t(`pur.import.result.${result.status}` as MessageKey));
    } catch (err) { setError(describeError(err)); } finally { setBusy(false); }
  };
  return <Modal title={t('pur.import.title')} className="purDialog" onClose={onClose}>
    <form className="purForm" noValidate onSubmit={(e) => void submit(e)}>
      <p className="muted">{t('pur.import.text')}</p>
      <label className="field" htmlFor={id}><span>{t('pur.import.label')}</span>
        <textarea id={id} dir="auto" rows={7} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} aria-describedby={`${id}-hint`} /></label>
      <p id={`${id}-hint`} className="muted purNote">{t('pur.import.safety')}</p>
      <fieldset className="purFieldset"><legend>{t('pur.form.ownership')}</legend>
        {(['personal', 'business'] as const).map((o) => <label key={o} className="purCheck"><input type="radio" name="pur-import-own" checked={ownership === o} onChange={() => setOwnership(o)} />{t(`pur.own.${o}.long` as MessageKey)}</label>)}
      </fieldset>
      <FormError id="pur-import-error" text={error} />
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('pur.saving') : t('pur.import.save')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ---- The order drawer --------------------------------------------------------------------------------------

function VaultFile({ documentId, label, icon }: { documentId: string | null; label: string; icon: ReactNode }) {
  const [doc, setDoc] = useState<Load<VaultDocument> | null>(null);
  useEffect(() => {
    if (!documentId) { setDoc(null); return; }
    let live = true;
    setDoc({ kind: 'loading' });
    vaultApi.getDocument(documentId).then((value) => { if (live) setDoc({ kind: 'ready', value }); }, (e) => { if (live) setDoc(failed(e)); });
    return () => { live = false; };
  }, [documentId]);
  return <div className={`purFile ${documentId ? 'has' : ''}`}>
    <span className="purFileIcon">{icon}</span>
    <span className="purFileText"><strong>{label}</strong>
      {!documentId && <small className="muted">{t('pur.file.none')}</small>}
      {doc?.kind === 'loading' && <small className="muted">{t('pur.loading')}</small>}
      {doc?.kind === 'ready' && <a href={documentHash(doc.value.id)} dir="auto">{doc.value.file?.name ?? doc.value.title}</a>}
      {(doc?.kind === 'error' || doc?.kind === 'denied') && <small className="muted">{t('pur.file.hidden')}</small>}
    </span>
    {doc?.kind === 'ready' && <Check aria-hidden="true" className="purOk" />}
  </div>;
}

function UploadToVault({ label, category, title, onUploaded, disabled }: { label: string; category: string; title: string; disabled?: boolean; onUploaded: (documentId: string) => Promise<void> }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const picked = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const doc = await vaultApi.upload(file, { title, category, access_class: 'private' });
      await onUploaded(doc.id);
    } catch (e) {
      const existing = vaultApi.duplicateOf(e);
      if (existing && !existing.inTrash) { try { await onUploaded(existing.id); } catch (inner) { setError(describeError(inner)); } }
      else setError(describeError(e));
    } finally { setBusy(false); if (input.current) input.current.value = ''; }
  };
  return <>
    <input ref={input} type="file" className="visuallyHidden" tabIndex={-1} aria-hidden="true" accept="image/*,application/pdf" onChange={(e) => void picked(e.target.files?.[0])} />
    <button type="button" className="secondary" disabled={busy || disabled} onClick={() => input.current?.click()}><Upload aria-hidden="true" />{busy ? t('pur.uploading') : label}</button>
    {error && <p className="cmFieldError" role="alert">{error}</p>}
  </>;
}

function Stepper({ purchase }: { purchase: PurchaseDetail }) {
  const steps = trackerSteps(purchase);
  return <ol className="purStepper" aria-label={t('pur.drawer.progress')}>
    {steps.map((step) => <li key={step.id} className={step.state} aria-current={step.state === 'current' ? 'step' : undefined}>
      <span className="purStepDot" aria-hidden="true">{step.state === 'done' ? <Check /> : null}</span>
      <strong>{step.label}</strong><small>{step.day ? shortDay(step.day) : step.state === 'current' ? t('pur.step.inProgress') : '-'}</small>
      <span className="visuallyHidden">{t(`pur.step.state.${step.state}` as MessageKey)}</span>
    </li>)}
  </ol>;
}

function Tracking({ shipment }: { shipment: PurchaseShipment }) {
  const [copied, setCopied] = useState(false);
  const url = carrierUrl(shipment.carrier, shipment.tracking_number);
  const carrier = carrierKey(shipment.carrier);
  return <FinCard title={t('pur.drawer.tracking')} icon={<Truck />} className="purDrawerCard"
    actions={url ? <a href={url} target="_blank" rel="noopener noreferrer" className="purExternal">{t('pur.drawer.viewOn', { carrier: shipment.carrier ?? carrier ?? '' })}<ExternalLink aria-hidden="true" /></a> : undefined}>
    <dl className="purFacts">
      <dt>{t('pur.drawer.trackingCode')}</dt>
      <dd>{shipment.tracking_number ? <><bdi dir="ltr" className="purMono">{maskTracking(shipment.tracking_number)}</bdi> <span className="muted">{t('pur.drawer.masked')}</span>
        <button type="button" className="purIcon" aria-label={t('pur.drawer.copyTracking')} onClick={() => void copyText(shipment.tracking_number!).then((ok) => setCopied(ok))}><Copy aria-hidden="true" /></button>
        {copied && <span role="status" className="muted">{t('pur.drawer.copied')}</span>}</> : '-'}</dd>
      <dt>{t('pur.ship.carrier')}</dt><dd dir="auto">{shipment.carrier ?? '-'}</dd>
      <dt>{t('pur.drawer.latest')}</dt><dd>{shipmentLabel(shipment.status)}{shipment.note ? <> · <bdi dir="auto">{shipment.note}</bdi></> : null}</dd>
      <dt>{shipment.delivered_on ? t('pur.ship.delivered') : t('pur.ship.expected')}</dt><dd>{shortDay(shipment.delivered_on ?? shipment.expected_on)}</dd>
    </dl>
    <p className="muted purNote">{t('pur.drawer.trackingRule')}</p>
  </FinCard>;
}

function ReturnCards({ purchase, ret }: { purchase: PurchaseDetail; ret: PurchaseReturn }) {
  const match = refundMatch(ret);
  const left = refundLeft(ret);
  return <>
    <FinCard title={t('pur.drawer.pickup')} icon={<Undo2 />} className="purDrawerCard"
      actions={<StatusChip tone={chipTone(ret.status)}>{returnStatusLabel(ret.status)}</StatusChip>}>
      <dl className="purFacts">
        <dt>{t('pur.ret.courier')}</dt><dd dir="auto">{ret.courier ?? '-'}</dd>
        <dt>{t('pur.ret.pickupOn')}</dt><dd>{shortDay(ret.pickup_on)}</dd>
        <dt>{t('pur.ret.tracking')}</dt><dd><bdi dir="ltr" className="purMono">{maskTracking(ret.tracking_number)}</bdi></dd>
        <dt>{t('pur.ret.deadline')}</dt><dd>{shortDay(ret.deadline_on)}</dd>
        {ret.confirmation_ref && <><dt>{t('pur.ret.confirmation')}</dt><dd dir="ltr">{ret.confirmation_ref}</dd></>}
      </dl>
    </FinCard>
    <div className="purFiles">
      <VaultFile documentId={ret.proof_document_id} label={t('pur.drawer.proof')} icon={<Camera aria-hidden="true" />} />
      <VaultFile documentId={purchase.document_id} label={t('pur.drawer.receipt')} icon={<FileText aria-hidden="true" />} />
    </div>
    <FinCard title={t('pur.drawer.refund')} icon={<Receipt />} className="purDrawerCard">
      <dl className="purFacts">
        <dt>{t('pur.drawer.destination')}</dt><dd>{purchase.payment_method ? t('pur.drawer.original', { method: methodLabel(purchase.payment_method) }) : '-'}</dd>
        <dt>{t('pur.drawer.expected')}</dt><dd dir="ltr">{ret.amount ? formatAmount(ret.amount, ret.currency) : '-'}</dd>
        <dt>{t('pur.drawer.refunded')}</dt><dd dir="ltr">{ret.refunded_all.length ? ret.refunded_all.map((r) => formatAmount(r.amount, r.currency)).join(', ') : '-'}
          {left && Number(left) > 0 && ret.refunded_all.length > 0 ? <span className="muted"> · {t('pur.drawer.left', { amount: formatAmount(left, ret.currency) })}</span> : null}</dd>
        <dt>{t('pur.drawer.match')}</dt>
        <dd><StatusChip tone={match === 'matched' ? 'ok' : match === 'review' ? 'warn' : 'muted'}>{t(`pur.match.${match}` as MessageKey)}</StatusChip>
          <small className="muted purMatchHint">{t(`pur.match.${match}.hint` as MessageKey)}</small></dd>
      </dl>
      {!ret.can_refund && ret.status !== 'refunded' && <p className="muted purNote">{t('pur.drawer.refundOwner')}</p>}
    </FinCard>
  </>;
}

export function PurchaseDrawer({ purchaseId, onClose, onChanged, onOpenFull }: {
  purchaseId: string; onClose: () => void; onChanged: () => void; onOpenFull: (id: string) => void;
}) {
  const [data, setData] = useState<Load<PurchaseDetail>>({ kind: 'loading' });
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const [dialog, setDialog] = useState<'edit' | 'ship' | 'refund' | 'cancel' | null>(null);
  const [busy, setBusy] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const load = useCallback(async () => {
    try { setData({ kind: 'ready', value: await api.getPurchase(purchaseId) }); } catch (e) { setData(failed(e)); }
  }, [purchaseId]);
  useEffect(() => { setData({ kind: 'loading' }); setMessage(null); void load(); }, [load]);
  useEffect(() => { heading.current?.focus(); }, [purchaseId]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !dialog) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [dialog, onClose]);
  const done = (text: string) => { setDialog(null); setMessage({ text }); onChanged(); void load(); };
  const p = data.kind === 'ready' ? data.value : null;
  const ret = p ? currentReturn(p) : null;
  const shipment = p?.shipments.length ? p.shipments[p.shipments.length - 1] : null;
  const name = p ? rowTitle({ merchant: p.merchant, first_item: p.items[0]?.description ?? null, item_count: p.items.length }) : '';
  const cancel = async () => {
    if (!p) return;
    setBusy(true);
    try { await api.moveStatus(p.id, { to: 'cancelled', version: p.version }); done(t('pur.drawer.cancelled')); }
    catch (e) { setDialog(null); setMessage({ text: describeError(e), error: true }); } finally { setBusy(false); }
  };
  return <aside className="purDrawer" aria-labelledby="pur-drawer-title">
    <div className="purDrawerHead">
      <div><h2 id="pur-drawer-title" ref={heading} tabIndex={-1} dir="auto">{p ? <><bdi>{name}</bdi> · <bdi>{p.merchant}</bdi></> : t('pur.drawer.title')}</h2>
        {p?.order_ref && <p className="muted">{t('pur.drawer.order', { ref: p.order_ref })}</p>}</div>
      <div className="purDrawerButtons">
        {p?.can_edit && <button type="button" className="secondary" onClick={() => setDialog('edit')}><Pencil aria-hidden="true" />{t('pur.edit')}</button>}
        {p?.can_edit && p.allowed_moves.includes('cancelled') && <button type="button" className="secondary" onClick={() => setDialog('cancel')}><X aria-hidden="true" />{t('pur.drawer.cancel')}</button>}
        <button type="button" className="purIcon" aria-label={t('pur.drawer.close')} onClick={onClose}><X aria-hidden="true" /></button>
      </div>
    </div>
    {data.kind === 'loading' && <Notice busy>{t('pur.loading')}</Notice>}
    {(data.kind === 'error' || data.kind === 'denied') && <LoadError message={t('pur.error.load', { message: data.message })} retry={() => void load()} />}
    {message && <Notice error={message.error}>{message.text}</Notice>}
    {p && <>
      <dl className="purDrawerFacts">
        <div><dt>{t('pur.drawer.total')}</dt><dd dir="ltr"><strong>{p.total ? formatAmount(p.total, p.currency) : '-'}</strong></dd></div>
        <div><dt>{t('pur.drawer.paidOn')}</dt><dd>{methodLabel(p.payment_method)}</dd></div>
        <div><dt>{t('pur.drawer.financeTx')}</dt><dd>{linkState(p.payment) === 'active'
          ? (p.payment?.canonical_id ? <a href="#finance-transactions" className="purOk"><Link2 aria-hidden="true" />{t('pur.drawer.linked')}</a> : <span className="purOk">{t('pur.drawer.linked')}</span>)
          : linkState(p.payment) === 'needs_review' ? <span className="purWarn">{t('pur.pay.reviewShort')}</span> : <span className="muted">{t('pur.pay.notLinked')}</span>}</dd></div>
      </dl>
      {ret && <p className="purReason"><span>{t('pur.drawer.reason')}</span><strong>{reasonLabel(ret.reason)}{ret.reason_note ? <> · <bdi dir="auto">{ret.reason_note}</bdi></> : null}</strong></p>}
      <Stepper purchase={p} />
      {ret && <ReturnCards purchase={p} ret={ret} />}
      {!ret && <div className="purFiles"><VaultFile documentId={p.document_id} label={t('pur.drawer.receipt')} icon={<FileText aria-hidden="true" />} /></div>}
      <div className="purDrawerActions">
        {p.can_edit && <button type="button" onClick={() => setDialog('ship')}><Truck aria-hidden="true" />{t('pur.drawer.carrierUpdate')}</button>}
        {ret?.can_edit && <UploadToVault label={t('pur.drawer.uploadProof')} category="other" title={t('pur.drawer.proofTitle', { name: purchaseName(p) })}
          onUploaded={async (documentId) => { await api.updateReturn(ret.id, { version: ret.version, proof_document_id: documentId }); done(t('pur.drawer.proofSaved')); }} />}
        {!ret && p.can_edit && <UploadToVault label={t('pur.drawer.uploadReceipt')} category="receipt" title={t('pur.drawer.receiptTitle', { name: purchaseName(p) })}
          onUploaded={async (documentId) => { await api.updatePurchase(p.id, { version: p.version, document_id: documentId }); done(t('pur.drawer.receiptSaved')); }} />}
        {ret?.can_refund && !['refunded', 'rejected', 'cancelled'].includes(ret.status) && <button type="button" className="secondary" onClick={() => setDialog('refund')}><Link2 aria-hidden="true" />{t('pur.drawer.linkRefund')}</button>}
      </div>
      {shipment ? <Tracking shipment={shipment} /> : <FinCard title={t('pur.drawer.tracking')} icon={<Truck />} className="purDrawerCard"><p className="muted">{t('pur.drawer.noShipment')}</p></FinCard>}
      <div className="purDrawerPair">
        <FinCard title={t('pur.drawer.links')} icon={<Link2 />} className="purDrawerCard">
          <dl className="purFacts">
            <dt>{t('pur.drawer.payment')}</dt><dd>{linkState(p.payment) === 'active' ? t('pur.drawer.linked') : t('pur.pay.notLinked')}</dd>
            <dt>{t('pur.drawer.source')}</dt><dd>{t(`pur.source.${p.source}` as MessageKey)}</dd>
            <dt>{t('pur.drawer.ownership')}</dt><dd>{t(`pur.own.${p.ownership}` as MessageKey)}</dd>
          </dl>
          <button type="button" className="linkButton" onClick={() => onOpenFull(p.id)}>{t('pur.drawer.full')}</button>
        </FinCard>
        <FinCard title={t('pur.drawer.history')} icon={<History />} className="purDrawerCard">
          {p.events.length === 0 ? <p className="muted">{t('pur.drawer.noHistory')}</p>
            : <ul className="purAudit">{[...p.events].slice(0, 6).map((event) => <li key={event.id}><span>{eventText(event)}</span><small>{stamp(event.occurred_at)}</small></li>)}</ul>}
        </FinCard>
      </div>
    </>}
    {dialog === 'edit' && p && <PurchaseForm purchase={p} onClose={() => setDialog(null)} onSaved={() => done(t('pur.saved'))} />}
    {dialog === 'ship' && p && <ShipmentForm purchaseId={p.id} shipment={shipment && shipment.can_edit ? shipment : null} onClose={() => setDialog(null)} onSaved={() => done(t('pur.drawer.shipSaved'))} />}
    {dialog === 'refund' && p && ret && <MoneyDialog mode="refund" targetId={ret.id} current={null}
      hint={{ day: ret.returned_on ?? ret.requested_on, amount: refundLeft(ret), currency: ret.currency }} onClose={() => setDialog(null)} onDone={done} />}
    {dialog === 'cancel' && p && <ConfirmDialog title={t('pur.drawer.cancelTitle')} confirmLabel={t('pur.drawer.cancel')} danger busy={busy}
      onConfirm={() => void cancel()} onCancel={() => setDialog(null)}><p>{t('pur.drawer.cancelText', { name: purchaseName(p) })}</p></ConfirmDialog>}
  </aside>;
}

export function RefundNote() {
  return <FinBanner tone="info" icon={<CircleAlert />}>{t('pur.refundNote')}</FinBanner>;
}
