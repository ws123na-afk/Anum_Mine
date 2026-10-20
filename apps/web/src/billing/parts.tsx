// Building blocks shared by the Billing settings page, the platform console and the public pages
// (docs/billing.md): notices, usage meters, plan cards, the invoice table and the invoice detail.
import { useId, type ReactNode } from 'react';
import { Check, CircleDot, Download, FileText, Search } from 'lucide-react';
import { formatDateTime, t, type MessageKey } from '../lib/i18n';
import {
  INVOICE_STATUSES, LIMIT_KEYS, formatMoney, formatRate, intervalKey, invoiceTone, limitValue, meterKey, meterValues, pageRange, usagePercent, usageTone,
  type Invoice, type InvoiceStatus, type InvoiceSummary, type Meter, type Plan,
} from '../lib/billing';
import './billing.css';

export function Note({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}
export function Badge({ tone, children }: { tone: string; children: ReactNode }) { return <span className={`statusBadge ${tone}`}>{children}</span>; }
export function Empty({ title, text, action, icon }: { title: string; text: string; action?: ReactNode; icon?: ReactNode }) {
  return <div className="emptyState blEmpty">{icon ?? <Search aria-hidden="true" />}<h3>{title}</h3><p>{text}</p>{action && <div className="actions blCenter">{action}</div>}</div>;
}
export const date = (value: string | null | undefined): string => value ? formatDateTime(value, { dateStyle: 'medium' }) : '-';

/** Usage meters with bars; an unlimited meter says so instead of a bar. */
export function Meters({ meters }: { meters: Meter[] }) {
  return <ul className="blMeters">{meters.map((meter) => {
    const percent = usagePercent(meter.used, meter.limit), values = meterValues(meter);
    const label = t(meterKey(meter.key));
    return <li key={meter.key} className={`blMeter ${usageTone(percent)}`}>
      <div className="blMeterHead"><span>{label}</span><strong>{values.limit === null ? t('billing.usage.unlimitedOf', { used: values.used }) : t('billing.usage.of', { used: values.used, limit: values.limit })}</strong></div>
      {percent === null ? <small className="muted">{t('billing.usage.unlimited')}</small>
        : <div className="blBar" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-valuetext={t('billing.usage.percent', { percent })}><span style={{ inlineSize: `${percent}%` }} /></div>}
    </li>;
  })}</ul>;
}

/** A plan's price line ("SAR 299.00 per month"). */
export function priceLine(plan: Pick<Plan, 'price_minor' | 'currency' | 'interval' | 'per_seat'>): string {
  return t(plan.per_seat ? 'billing.pricePerSeat' : 'billing.price', { price: formatMoney(plan.price_minor, plan.currency), interval: t(intervalKey(plan.interval)) });
}

/** The limits of a plan as short lines ("10 seats", "Unlimited workspaces"). */
export function limitLines(plan: Pick<Plan, 'limits'>): string[] {
  return LIMIT_KEYS.map((key) => {
    const value = limitValue(key, plan.limits?.[key]);
    return value === null ? t('billing.limit.unlimited', { what: t(meterKey(key)) }) : t('billing.limit.value', { value, what: t(meterKey(key)) });
  });
}

/** A plan card for the pricing page and the change plan dialog. */
export function PlanCard({ plan, action, badge }: { plan: Plan; action?: ReactNode; badge?: ReactNode }) {
  const titleId = useId();
  return <article className={`surface blPlan ${plan.current ? 'current' : ''}`} aria-labelledby={titleId}>
    <header><h3 id={titleId} dir="auto">{plan.name}</h3>{badge}</header>
    <p className="blPrice">{priceLine(plan)}</p>
    {plan.trial_days > 0 && <p className="blTrial">{t('billing.trialDays', { count: plan.trial_days })}</p>}
    {plan.description && <p className="muted" dir="auto">{plan.description}</p>}
    <ul className="blFeatures">{[...limitLines(plan), ...plan.features].map((line, index) => <li key={`${index}-${line}`}><Check aria-hidden="true" /><span dir="auto">{line}</span></li>)}</ul>
    {action && <div className="actions blPlanAction">{action}</div>}
  </article>;
}

export function Pager({ total, offset, shown, onPrev, onNext }: { total: number; offset: number; shown: number; onPrev: () => void; onNext: () => void }) {
  const range = pageRange(total, offset, shown);
  if (!total) return null;
  return <nav className="blPager" aria-label={t('billing.paging')}>
    <span className="muted" role="status">{t('billing.range', { from: range.from, to: range.to, total })}</span>
    <button type="button" className="secondary" disabled={!range.hasPrev} onClick={onPrev}>{t('billing.prev')}</button>
    <button type="button" className="secondary" disabled={!range.hasNext} onClick={onNext}>{t('billing.next')}</button>
  </nav>;
}

export const invoiceStatusName = (status: InvoiceStatus | string): string => t(`billing.invoiceStatus.${status}` as MessageKey);
export const invoiceLabel = (invoice: Pick<InvoiceSummary, 'number' | 'status'>): string => invoice.number ?? t('billing.invoice.draftNumber');

/** The invoice list's search and status filter. */
export function InvoiceFilters({ query, status, onQuery, onStatus }: { query: string; status: InvoiceStatus | ''; onQuery: (value: string) => void; onStatus: (value: InvoiceStatus | '') => void }) {
  const searchId = useId(), statusId = useId();
  return <div className="blToolbar" role="search">
    <label className="field blSearch" htmlFor={searchId}><span>{t('billing.invoices.search')}</span>
      <input id={searchId} type="search" value={query} dir="ltr" placeholder="INV-000001" onChange={(event) => onQuery(event.target.value)} /></label>
    <label className="field blFilter" htmlFor={statusId}><span>{t('billing.invoices.status')}</span>
      <select id={statusId} value={status} onChange={(event) => onStatus(event.target.value as InvoiceStatus | '')}>
        <option value="">{t('billing.all')}</option>{INVOICE_STATUSES.map((value) => <option key={value} value={value}>{invoiceStatusName(value)}</option>)}
      </select></label>
  </div>;
}

/** The invoices of one organisation, newest first. */
export function InvoiceTable({ items, caption, onOpen, onDownload }: { items: InvoiceSummary[]; caption: string; onOpen: (invoice: InvoiceSummary) => void; onDownload: (invoice: InvoiceSummary) => void }) {
  return <div className="pane dataTableWrap"><table className="dataTable blTable">
    <caption className="visuallyHidden">{caption}</caption>
    <thead><tr>
      <th scope="col">{t('billing.col.invoice')}</th><th scope="col">{t('billing.col.status')}</th><th scope="col" className="optional">{t('billing.col.issued')}</th>
      <th scope="col" className="optional">{t('billing.col.due')}</th><th scope="col" className="num blWideOnly">{t('billing.col.total')}</th><th scope="col" className="num">{t('billing.col.amountDue')}</th>
      <th scope="col"><span className="visuallyHidden">{t('billing.col.actions')}</span></th>
    </tr></thead>
    <tbody>{items.map((invoice) => <tr key={invoice.id}>
      <th scope="row"><strong dir="ltr">{invoiceLabel(invoice)}</strong><small>{t(`billing.invoiceKind.${invoice.kind}` as MessageKey)}</small></th>
      <td><Badge tone={invoiceTone(invoice)}>{invoice.overdue ? t('billing.overdue') : invoiceStatusName(invoice.status)}</Badge></td>
      <td className="optional nowrap">{date(invoice.issued_at)}</td>
      <td className="optional nowrap">{date(invoice.due_at)}</td>
      <td className="num blWideOnly">{formatMoney(invoice.total_minor, invoice.currency)}</td>
      <td className="num">{formatMoney(invoice.amount_due_minor, invoice.currency)}</td>
      <td><div className="cellActions">
        <button type="button" className="secondary" onClick={() => onOpen(invoice)} aria-label={t('billing.invoices.openOf', { number: invoiceLabel(invoice) })}><FileText aria-hidden="true" />{t('billing.invoices.open')}</button>
        {invoice.number && <button type="button" className="secondary iconOnly" onClick={() => onDownload(invoice)} aria-label={t('billing.invoices.pdfOf', { number: invoiceLabel(invoice) })} title={t('billing.invoices.pdf')}><Download aria-hidden="true" /></button>}
      </div></td>
    </tr>)}</tbody>
  </table></div>;
}

/** An invoice: parties, lines, VAT with its rate, totals, payments, credit notes and the bank details. */
export function InvoiceBody({ invoice }: { invoice: Invoice }) {
  const money = (minor: number) => formatMoney(minor, invoice.currency);
  const bill = invoice.bill_to ?? {}, issuer = invoice.issuer ?? {};
  const address = (row: Record<string, string>) => [row.company_name || row.display_name, row.address_line1, row.address_line2, [row.postal_code, row.city].filter(Boolean).join(' '), row.country].filter(Boolean);
  return <div className="blInvoice">
    <div className="blInvoiceHead">
      <div><h3 dir="ltr">{invoiceLabel(invoice)}</h3><Badge tone={invoiceTone(invoice)}>{invoice.overdue ? t('billing.overdue') : invoiceStatusName(invoice.status)}</Badge></div>
      <dl className="blFacts">
        <div><dt>{t('billing.invoice.issued')}</dt><dd>{date(invoice.issued_at)}</dd></div>
        <div><dt>{t('billing.invoice.due')}</dt><dd>{date(invoice.due_at)}</dd></div>
        <div><dt>{t('billing.invoice.period')}</dt><dd>{invoice.period_start ? t('billing.invoice.periodValue', { from: date(invoice.period_start), to: date(invoice.period_end) }) : '-'}</dd></div>
      </dl>
    </div>
    <div className="blParties">
      <section><h4>{t('billing.invoice.from')}</h4><p dir="auto">{issuer.issuer_name || '-'}</p>{issuer.issuer_address && <p className="muted blPre" dir="auto">{issuer.issuer_address}</p>}{issuer.issuer_vat_number && <p className="muted">{t('billing.invoice.vatNumber', { number: issuer.issuer_vat_number })}</p>}</section>
      <section><h4>{t('billing.invoice.billTo')}</h4>{address(bill).length ? address(bill).map((line, i) => <p key={i} dir="auto" className={i ? 'muted' : undefined}>{line}</p>) : <p>-</p>}{bill.vat_number && <p className="muted">{t('billing.invoice.vatNumber', { number: bill.vat_number })}</p>}</section>
    </div>
    <div className="dataTableWrap"><table className="dataTable blLines">
      <caption className="visuallyHidden">{t('billing.invoice.lines')}</caption>
      <thead><tr><th scope="col">{t('billing.invoice.description')}</th><th scope="col" className="num">{t('billing.invoice.quantity')}</th><th scope="col" className="num optional">{t('billing.invoice.unit')}</th><th scope="col" className="num">{t('billing.invoice.amount')}</th></tr></thead>
      <tbody>{invoice.lines.map((line) => <tr key={line.id}><td dir="auto">{line.description}</td><td className="num">{line.quantity}</td><td className="num optional">{money(line.unit_minor)}</td><td className="num">{money(line.amount_minor)}</td></tr>)}</tbody>
    </table></div>
    <dl className="blTotals">
      <div><dt>{t('billing.invoice.subtotal')}</dt><dd>{money(invoice.subtotal_minor)}</dd></div>
      <div><dt>{t('billing.invoice.vat', { rate: formatRate(invoice.tax_rate_bp), country: invoice.tax_country ?? '-' })}</dt><dd>{money(invoice.tax_minor)}</dd></div>
      <div className="blGrand"><dt>{t('billing.invoice.total')}</dt><dd>{money(invoice.total_minor)}</dd></div>
      {invoice.amount_paid_minor > 0 && <div><dt>{t('billing.invoice.paid')}</dt><dd>{money(invoice.amount_paid_minor)}</dd></div>}
      {invoice.amount_credited_minor > 0 && <div><dt>{t('billing.invoice.credited')}</dt><dd>{money(invoice.amount_credited_minor)}</dd></div>}
      <div className="blGrand"><dt>{t('billing.invoice.amountDue')}</dt><dd>{money(invoice.amount_due_minor)}</dd></div>
    </dl>
    <section><h4>{t('billing.invoice.payments')}</h4>{invoice.payments.length ? <ul className="blList">{invoice.payments.map((p) => <li key={p.id}><strong>{money(p.amount_minor)}</strong><span>{t(`billing.method.${p.method}` as MessageKey)}</span><span className="muted">{date(p.received_at)}</span>{p.reference && <span className="muted" dir="auto">{t('billing.invoice.reference', { reference: p.reference })}</span>}{p.has_proof && <span className="muted">{t('billing.invoice.proof', { name: p.proof_name ?? '-' })}</span>}</li>)}</ul> : <p className="muted">{t('billing.invoice.noPayments')}</p>}</section>
    {invoice.credit_notes.length > 0 && <section><h4>{t('billing.invoice.creditNotes')}</h4><ul className="blList">{invoice.credit_notes.map((n) => <li key={n.id}><strong dir="ltr">{n.number}</strong><span>{money(n.total_minor)}</span><span className="muted">{date(n.issued_at)}</span><span className="muted" dir="auto">{n.reason}</span></li>)}</ul></section>}
    {invoice.status === 'open' && <BankDetailsBlock bank={invoice.bank_details} reference={invoice.number} />}
  </div>;
}

/** Where to pay by bank transfer, with the invoice number as the reference. */
export function BankDetailsBlock({ bank, reference }: { bank: Invoice['bank_details']; reference?: string | null }) {
  if (!bank?.enabled) return <section className="blBank"><h4>{t('billing.bank.title')}</h4><p className="muted">{t('billing.bank.none')}</p></section>;
  return <section className="blBank"><h4>{t('billing.bank.title')}</h4>
    <dl className="blFacts">
      <div><dt>{t('billing.bank.bank')}</dt><dd dir="auto">{bank.bank_name || '-'}</dd></div>
      <div><dt>{t('billing.bank.accountName')}</dt><dd dir="auto">{bank.account_name || '-'}</dd></div>
      <div><dt>{t('billing.bank.iban')}</dt><dd><code dir="ltr">{bank.iban}</code></dd></div>
      {bank.swift && <div><dt>{t('billing.bank.swift')}</dt><dd><code dir="ltr">{bank.swift}</code></dd></div>}
      {reference && <div><dt>{t('billing.bank.reference')}</dt><dd><code dir="ltr">{reference}</code></dd></div>}
    </dl>
  </section>;
}
