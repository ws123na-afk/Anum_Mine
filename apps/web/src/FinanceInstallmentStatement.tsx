// Complete installment statement viewer. Source-reported "paid" is not a bank transaction.
// Keep canonical payment matches separate and never count refund-review amounts as payable.
import { Fragment, useEffect, useMemo, useState } from 'react';
import type { FinanceBill, FinanceBillOccurrence, FinanceInstallmentStatementEntry } from '@anum/contracts';
import * as plan from './lib/financePlanningApi';
import { InstallmentBillsSummary } from './InstallmentBillsSummary';
import { orderRefundState, rowDue, rowEffective } from './lib/financeRefunds';
import { t, type MessageKey } from './lib/i18n';

// A refunded installment (applied in Refunds, or marked so by the statement) is not due and never payable.
type Line = FinanceInstallmentStatementEntry & { verified: boolean; effective: 'paid' | 'scheduled' | 'review' | 'refunded'; due: number };
type Order = { key: string; merchant: string; reference: string; currency: string; purchaseDate: string | null; totalPlan: number;
  paid: number; verified: number; pending: number; review: number; refunded: number; paidAmount: number; pendingAmount: number; reviewAmount: number;
  refundedAmount: number; nextDue: string | null; lastDue: string | null; rows: Line[]; refundState: 'active' | 'partly_refunded' | 'refunded' };
const amount = (n: number, currency: string) => new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(n);
const keyOf = (r: FinanceInstallmentStatementEntry) => [r.provider.toLowerCase(), r.order_id.toLowerCase()].join('/');
const parseNotes = (notes: string | null, label: string) => notes?.split('|').map(s => s.trim()).find(s => s.startsWith(label + ': '))?.slice(label.length + 2) || '';

function csvCell(raw: string): string {
  const value = /^[=+\-@\t\r]/.test(raw) ? "'" + raw : raw;
  return '"' + value.replace(/"/g, '""') + '"';
}
function downloadStatement(rows: Line[]) {
  const headings = ['Provider','Merchant','Order ID','Purchase Date','Installment','Total Installments','Due Date','Amount','Currency','Source Status','Refunded Amount','Still Due','Verified Match','Source Email','Review Notes'];
  const values = rows.map(r => [r.provider,r.merchant,r.order_id,r.purchase_on ?? '',String(r.installment),String(r.total_installments),
    r.due_on,r.amount,r.currency,r.source_status,r.refunded_amount ?? '0',r.due.toFixed(2),r.verified ? 'Yes' : 'No',r.source_email ?? '',r.review_notes ?? '']);
  const csv = '\uFEFF' + [headings,...values].map(row => row.map(csvCell).join(',')).join('\r\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = 'ANUM_installment_statement.csv'; link.click();
  URL.revokeObjectURL(url);
}

export function FinanceInstallmentStatement({ bills, today, refreshKey = 0, onRefresh }: { bills: FinanceBill[]; today: string; refreshKey?: number; onRefresh?: () => void }) {
  const [entries, setEntries] = useState<FinanceInstallmentStatementEntry[] | null>(null);
  const [matched, setMatched] = useState<FinanceBillOccurrence[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [needle, setNeedle] = useState('');
  const [filter, setFilter] = useState<'all'|'outstanding'|'completed'|'review'|'refunded'>('all');
  const [expanded, setExpanded] = useState<string | null>(null);
  const billSignature = bills.map(b => b.id + ':' + b.updated_at + ':' + b.active).join(';');
  useEffect(() => {
    let cancelled = false; setLoading(true); setError('');
    plan.installmentStatement().then(async result => {
      const years = [...new Set(result.items.map(r => r.due_on.slice(0, 4)))];
      const schedules = await Promise.all(years.map(y => plan.billSchedule(y + '-01-01', y + '-12-31')));
      if (!cancelled) { setEntries(result.items); setMatched(schedules.flatMap(s => s.items)); }
    }).catch(() => { if (!cancelled) { setError('The full statement could not be loaded. Existing bill records are shown below.'); setEntries(null); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [revision, refreshKey, billSignature]);

  const lines = useMemo((): Line[] => {
    if (!entries) return [];
    const matchedMap = new Map(matched.map(o => [o.bill_id + '/' + o.due_on, o.status]));
    const billsByInstallment = new Map<string, FinanceBill>();
    for (const bill of bills) {
      const order = parseNotes(bill.notes, 'Order');
      const part = Number(parseNotes(bill.notes, 'Installment'));
      if (order && Number.isInteger(part) && part > 0) billsByInstallment.set([order.toLowerCase(), part].join('/'), bill);
    }
    return entries.map(r => {
      const bill = billsByInstallment.get([r.order_id.toLowerCase(), r.installment].join('/'));
      const verified = !!bill && matchedMap.get(bill.id + '/' + r.due_on) === 'paid';
      const effective = rowEffective(r, verified);
      return { ...r, verified, effective, due: effective === 'paid' ? 0 : rowDue(r) };
    });
  }, [entries, matched, bills]);

  const orders = useMemo(() => {
    const groups = new Map<string, Order>();
    for (const entry of lines) {
      const key = keyOf(entry);
      const group = groups.get(key) ?? { key, merchant: entry.merchant, reference: entry.order_id, currency: entry.currency,
        purchaseDate: entry.purchase_on, totalPlan: 0, paid: 0, verified: 0, pending: 0, review: 0, refunded: 0,
        paidAmount: 0, pendingAmount: 0, reviewAmount: 0, refundedAmount: 0, nextDue: null, lastDue: null, rows: [], refundState: 'active' as const };
      const value = Number(entry.amount);
      group.totalPlan += value;
      group.rows.push(entry);
      if (entry.verified) group.verified++;
      if (entry.effective === 'paid') { group.paid++; group.paidAmount += value; }
      else if (entry.effective === 'refunded') { group.refunded++; group.refundedAmount += value; }
      else if (entry.effective === 'scheduled') {
        // A partly refunded installment is still due, at its lower amount.
        group.pending++; group.pendingAmount += entry.due; group.refundedAmount += value - entry.due;
        if (!group.nextDue || entry.due_on < group.nextDue) group.nextDue = entry.due_on;
      } else { group.review++; group.reviewAmount += entry.due; group.refundedAmount += value - entry.due; }
      if (!group.lastDue || entry.due_on > group.lastDue) group.lastDue = entry.due_on;
      groups.set(key, group);
    }
    return [...groups.values()].map(g => ({ ...g, rows: g.rows.sort((a,b) => a.installment - b.installment), refundState: orderRefundState(g.rows) }))
      .sort((a,b) => (a.nextDue ?? a.lastDue ?? '').localeCompare(b.nextDue ?? b.lastDue ?? ''));
  }, [lines]);

  const shown = orders.filter(o => {
    if (filter === 'outstanding' && !o.pending) return false;
    if (filter === 'completed' && (o.pending || o.review)) return false;
    if (filter === 'review' && !o.review) return false;
    if (filter === 'refunded' && o.refundState === 'active') return false;
    const q = needle.trim().toLowerCase();
    return !q || (o.merchant + ' ' + o.reference).toLowerCase().includes(q);
  });
  const totals = useMemo(() => {
    const byCurrency = new Map<string, {paid:number,pending:number,review:number,refunded:number}>();
    for (const o of orders) {
      const sum = byCurrency.get(o.currency) ?? {paid:0,pending:0,review:0,refunded:0};
      sum.paid += o.paidAmount; sum.pending += o.pendingAmount; sum.review += o.reviewAmount; sum.refunded += o.refundedAmount;
      byCurrency.set(o.currency,sum);
    }
    return [...byCurrency].map(([currency,counts]) => ({currency,...counts}));
  }, [orders]);
  const months = useMemo(() => {
    const grouped = new Map<string, Map<string, number>>();
    for (const l of lines) if (l.effective === 'scheduled') {
      const m = l.due_on.slice(0,7), by = grouped.get(m) ?? new Map<string,number>();
      by.set(l.currency, (by.get(l.currency) ?? 0) + l.due); grouped.set(m, by);
    }
    return [...grouped].sort((a,b) => a[0].localeCompare(b[0]));
  }, [lines]);
  const overdue = lines.filter(l => l.effective === 'scheduled' && l.due_on < today).length;
  const next = lines.filter(l => l.effective === 'scheduled' && l.due_on >= today).sort((a,b)=>a.due_on.localeCompare(b.due_on))[0];

  if (loading && entries === null) return <p role="status">Loading complete installment statement…</p>;
  if (!entries?.length) return <>
    {error && <p role="alert">{error}</p>}
    <InstallmentBillsSummary bills={bills} onRefresh={onRefresh} />
  </>;
  return <section className="finCard" aria-label="Complete installment statement" style={{ marginBlock: 16, padding: 16 }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
      <div><h2>Complete installment statement</h2><p className="muted">All imported orders, including completed purchases and refund cases. Statuses reported by the provider are not bank-verified payments.</p></div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button type="button" disabled={loading} onClick={() => { onRefresh?.(); setRevision(x => x + 1); }}>Refresh</button>
        <button type="button" onClick={() => downloadStatement(lines)}>Export statement CSV</button>
      </div>
    </div>
    {error && <p role="alert">{error}</p>}
    <div className="finTableWrap"><table className="finGrid">
      <thead><tr><th>Orders</th><th>Installments</th><th>Reported paid</th><th>Verified matches</th><th>Scheduled</th><th>Refund / review</th><th>Overdue</th><th>Next due</th></tr></thead>
      <tbody><tr><td>{orders.length}</td><td>{lines.length}</td><td>{lines.filter(l => l.effective === 'paid').length}</td>
        <td>{lines.filter(l => l.verified).length}</td><td>{lines.filter(l => l.effective === 'scheduled').length}</td>
        <td>{lines.filter(l => l.effective === 'review').length}</td><td>{overdue}</td><td>{next?.due_on ?? 'None'}</td></tr></tbody>
    </table></div>
    <div className="finTableWrap"><table className="finGrid">
      <thead><tr><th>Currency</th><th>Reported paid amount</th><th>Scheduled outstanding</th><th>Amount under review</th><th>{t('frefund.statementRefundedAmount')}</th><th>Statement total*</th></tr></thead>
      <tbody>{totals.map(x => <tr key={x.currency}><td>{x.currency}</td><td>{amount(x.paid,x.currency)}</td>
        <td>{amount(x.pending,x.currency)}</td><td>{amount(x.review,x.currency)}</td><td>{amount(x.refunded,x.currency)}</td><td>{amount(x.paid + x.pending + x.review + x.refunded,x.currency)}</td></tr>)}</tbody>
    </table></div>
    <div style={{ display:'flex',gap:8, flexWrap:'wrap', marginBlock:12 }}>
      <label className="field"><span>Search purchase or order</span><input value={needle} onChange={e => setNeedle(e.target.value)} placeholder="Merchant / order number" /></label>
      <label className="field"><span>Show</span><select value={filter} onChange={e => setFilter(e.target.value as typeof filter)}>
        <option value="all">All purchases</option><option value="outstanding">Pending payments</option><option value="completed">Completed</option><option value="review">Refunds / review</option><option value="refunded">{t('frefund.filter.refunded')}</option></select></label>
    </div>
    <div className="finTableWrap"><table className="finGrid">
      <thead><tr><th>Purchase / order</th><th>{t('frefund.col.status')}</th><th>Purchase date</th><th>Paid</th><th>Pending</th><th>Review</th><th>Paid amount</th><th>Outstanding</th><th>Statement total*</th><th>Next due</th><th>Final due</th><th>Details</th></tr></thead>
      <tbody>{shown.map(o => <Fragment key={o.key}>
        <tr key={o.key}><td><strong>{o.merchant}</strong><small style={{display:'block'}}>{o.reference}</small></td>
          <td>{t(`frefund.state.${o.refundState}` as MessageKey)}</td>
          <td>{o.purchaseDate ?? 'Unknown'}</td><td>{o.paid} / {o.rows.length}</td><td>{o.pending}</td><td>{o.review}</td>
          <td>{amount(o.paidAmount,o.currency)}</td><td>{amount(o.pendingAmount,o.currency)}</td><td>{amount(o.totalPlan,o.currency)}</td>
          <td>{o.nextDue ?? 'None'}</td><td>{o.lastDue ?? 'None'}</td>
          <td><button type="button" aria-expanded={expanded === o.key} onClick={() => setExpanded(expanded === o.key ? null : o.key)}>View</button></td>
        </tr>
        {expanded === o.key && <tr key={o.key + '-expanded'}><td colSpan={12}>
          <div className="finTableWrap"><table className="finGrid"><thead><tr><th>#</th><th>Due</th><th>Amount</th><th>{t('frefund.statementRefundedAmount')}</th><th>Provider status</th><th>ANUM match</th><th>Review notes</th><th>Source</th></tr></thead>
            <tbody>{o.rows.map(l => <tr key={l.installment}><td>{l.installment} / {l.total_installments}</td><td>{l.due_on}</td>
              <td>{amount(Number(l.amount),l.currency)}</td><td>{amount(Number(l.amount) - (l.effective === 'paid' ? Number(l.amount) : l.due),l.currency)}</td>
              <td>{l.effective === 'refunded' ? t('frefund.inst.refunded') : l.source_status}</td>
              <td>{l.verified ? 'Verified transaction match' : 'Not matched'}</td><td>{l.review_notes ?? ''}</td><td>{l.source_email?.startsWith('https://mail.google.com/') ? <a href={l.source_email} target="_blank" rel="noreferrer">Email</a> : 'Not linked'}</td></tr>)}</tbody>
          </table></div>
        </td></tr>}
      </Fragment>)}</tbody>
    </table></div>
    <h3>Scheduled by month</h3>
    <div className="finTableWrap"><table className="finGrid"><thead><tr><th>Month</th><th>Scheduled due</th><th>Overdue / upcoming</th></tr></thead>
      <tbody>{months.map(([month, currencies]) => <tr key={month}><td>{month}</td><td>{[...currencies].map(([currency,sum])=>amount(sum,currency)).join(' · ')}</td>
        <td>{month < today.slice(0,7) ? 'Past due, check status' : month === today.slice(0,7) ? 'This month' : 'Upcoming'}</td></tr>)}</tbody>
    </table></div>
    <p className="muted">* Statement totals sum the uploaded installment rows before disputed refund adjustments; they are not a verified merchant invoice total. Amounts under review are excluded from payable totals. Never automatically mark an installment paid from an email alone. Verify actual payments against the canonical ledger. This page does not transfer money.</p>
  </section>;
}
