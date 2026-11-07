import { useEffect, useMemo, useState } from 'react';
import type { FinanceBill, FinanceBillInput, FinanceInstallmentStatementEntry } from '@anum/contracts';
import * as billsApi from './lib/financeApi';
import { syncEmailAccount } from './lib/emailApi';
import * as plan from './lib/financePlanningApi';
import { describeError } from './lib/errors';

const TARGET = 'shahzeb.rahmanllc@gmail.com';
type Preview = plan.TamaraGmailPreview;
const format = (n: number) => new Intl.NumberFormat(undefined, { style: 'currency', currency: 'SAR' }).format(n);
const orderKey = (r: FinanceInstallmentStatementEntry) => r.provider.toLowerCase() + '/' + r.order_id.toLowerCase() + '/' + r.installment;

export function FinanceTamaraGmailSync({ existing, onDone, onClose }: {
  existing: FinanceBill[]; onDone: () => void; onClose: () => void;
}) {
  const [mailboxes, setMailboxes] = useState<plan.FinanceGmailMailbox[]>([]);
  const [account, setAccount] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [saved, setSaved] = useState<FinanceInstallmentStatementEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState('');

  useEffect(() => {
    let active = true;
    Promise.all([plan.installmentGmailMailboxes(), plan.installmentStatement()])
      .then(([found, statement]) => {
        if (!active) return;
        setMailboxes(found);
        setSaved(statement.items);
        setAccount(found.find(m => m.address.toLowerCase() === TARGET)?.id ?? found[0]?.id ?? '');
      })
      .catch(e => { if (active) setError(describeError(e)); });
    return () => { active = false; };
  }, []);

  async function examine() {
    if (!account) return;
    setLoading(true); setError(''); setDone(''); setPreview(null);
    try {
      await syncEmailAccount(account);
      const found = await plan.previewTamaraGmail(account, 500);
      setPreview(found);
    } catch (e) { setError(describeError(e)); }
    finally { setLoading(false); }
  }

  const reconciliation = useMemo(() => {
    if (!preview) return { add: [] as FinanceInstallmentStatementEntry[], review: [] as string[], paused: [] as FinanceBill[] };
    const history = new Map(saved.map(r => [orderKey(r), r]));
    const billsByKey = new Map<string, FinanceBill>();
    for (const b of existing) {
      const id = b.notes?.match(/(?:^|\|\s*)Order:\s*([^|]+)/i)?.[1]?.trim();
      const idx = b.notes?.match(/(?:^|\|\s*)Installment:\s*([^|]+)/i)?.[1]?.trim();
      if (id && idx) billsByKey.set('tamara/' + id.toLowerCase() + '/' + Number(idx), b);
    }
    const add: FinanceInstallmentStatementEntry[] = [], review: string[] = [], paused: FinanceBill[] = [];
    for (const row of preview.rows) {
      const old = history.get(orderKey(row));
      if (old && (old.due_on !== row.due_on || Number(old.amount) !== Number(row.amount))) {
        review.push(row.merchant + ' / ' + row.order_id + ' #' + row.installment + ': due date or amount changed'); continue;
      }
      const found = billsByKey.get(orderKey(row));
      if (row.source_status === 'scheduled' && !found) add.push(row);
      if (found && row.source_status !== 'scheduled' && found.active) paused.push(found);
      if (found && row.source_status === 'scheduled' && (found.next_due_on !== row.due_on || Number(found.amount) !== Number(row.amount)))
        review.push(row.order_id + ' #' + row.installment + ': existing Bill differs from the email');
    }
    return { add, review, paused };
  }, [preview, existing, saved]);

  const totals = useMemo(() => (preview?.rows ?? []).reduce((acc, r) => {
    acc[r.source_status].count++;
    acc[r.source_status].amount += Number(r.amount);
    return acc;
  }, { paid: { count: 0, amount: 0 }, scheduled: { count: 0, amount: 0 }, review: { count: 0, amount: 0 } }), [preview]);

  async function apply() {
    if (!preview || busy || reconciliation.review.length) return;
    setBusy(true); setError(''); setDone('');
    let created = 0, paused = 0;
    try {
      const sync = await plan.syncInstallmentStatement(preview.rows);
      if (sync.conflicts.length) {
        setError('Statement changes require manual reconciliation: ' + sync.conflicts.join('; ')); return;
      }
      // Never mark bank payments paid. For source-reported refunds / payments, pause only
      // bills previously imported from the Tamara feed, without deleting matched transactions.
      for (const b of reconciliation.paused) {
        await billsApi.updateBill(b.id, { name: b.name, amount: b.amount, currency: b.currency,
          cadence: b.cadence, next_due_on: b.next_due_on, category: b.category,
          account_id: b.account_id, source: b.source, active: false,
          notes: (b.notes ?? '').slice(0, 870) + ' | Email status: review or source-reported paid' });
        paused++;
      }
      for (const r of reconciliation.add) {
        const name = `Tamara | ${r.merchant} | ${r.installment}`.slice(0, 120);
        const body: FinanceBillInput = {
          name, currency: r.currency, amount: r.amount, cadence: 'once', next_due_on: r.due_on,
          category: 'shopping', active: true, source: 'manual',
          notes: `Order: ${r.order_id} | Installment: ${r.installment} | Gmail source: ${r.source_email ?? 'not available'} | Provider-reported scheduled; no bank match confirmed.`.slice(0, 1000),
        };
        await billsApi.createBill(body); created++;
      }
      setDone(`Saved ${preview.rows.length} email installment entries; created ${created} new Bills and paused ${paused} source-paid/review Bills. No bank transactions were created.`);
      onDone();
    } catch (e) {
      setError(`Partial synchronization: ${created} new Bills, ${paused} paused. ${describeError(e)}. Re-open the tool and refresh the preview before retrying.`);
      onDone();
    } finally { setBusy(false); }
  }

  return <div className="finForm">
    <p>This first refreshes the selected Gmail mailbox in ANUM Email, then reads Tamara receipts. Email payments are only provider-reported until matched with actual bank records. Manual Bills and CSV Import remain available.</p>
    <label className="field"><span>Gmail mailbox</span>
      <select value={account} onChange={e => { setAccount(e.target.value); setPreview(null); }}>
        {mailboxes.length === 0 && <option value="">No connected Gmail account</option>}
        {mailboxes.map(m => <option key={m.id} value={m.id}>{m.address}{m.address.toLowerCase() === TARGET ? ' (LLC preferred)' : ''}</option>)}
      </select>
    </label>
    {!mailboxes.some(m => m.address.toLowerCase() === TARGET) &&
      <p role="status">The LLC Gmail account ({TARGET}) is not connected to ANUM Email yet. Connect it there with a private mailbox grant, then return to Finance.</p>}
    <div className="finFormActions">
      <button type="button" disabled={loading || busy || !account} onClick={() => void examine()}>{loading ? 'Reading Gmail…' : 'Preview Tamara emails'}</button>
      <button type="button" onClick={onClose}>Close</button>
    </div>
    {preview && <>
      <h3>Preview from {preview.address}</h3>
      <p>Messages checked: {preview.messages_checked}. Tamara messages read: {preview.messages_matched}. Installments found: {preview.rows.length}.</p>
      <div className="finTableWrap"><table className="finGrid">
        <thead><tr><th>Status from Gmail</th><th>Installments</th><th>Amount</th></tr></thead>
        <tbody>{(['paid', 'scheduled', 'review'] as const).map(status => <tr key={status}><td>{status}</td><td>{totals[status].count}</td><td>{format(totals[status].amount)}</td></tr>)}</tbody>
      </table></div>
      <p role="status">New Bills: {reconciliation.add.length}. Existing Bills to pause: {reconciliation.paused.length}. Conflicts: {reconciliation.review.length}.</p>
      {preview.warnings.length > 0 && <details><summary>{preview.warnings.length} parsing or coverage warnings</summary>
        <ul>{preview.warnings.slice(0, 30).map((s,i) => <li key={i}>{s}</li>)}</ul></details>}
      {reconciliation.review.length > 0 && <div role="alert"><strong>Manual reconciliation needed:</strong>
        <ul>{reconciliation.review.map((s,i) => <li key={i}>{s}</li>)}</ul></div>}
      <div className="finTableWrap"><table className="finGrid"><thead><tr><th>Purchase</th><th>Installment</th><th>Due</th><th>Amount</th><th>Email status</th></tr></thead>
        <tbody>{preview.rows.slice(0, 60).map(r => <tr key={orderKey(r)}><td>{r.merchant}<small style={{ display: 'block' }}>{r.order_id}</small></td>
          <td>{r.installment}/{r.total_installments}</td><td>{r.due_on}</td><td>{format(Number(r.amount))}</td><td>{r.source_status}</td></tr>)}</tbody>
      </table>{preview.rows.length > 60 && <p>Showing 60 of {preview.rows.length} entries.</p>}</div>
      <button type="button" disabled={busy || !preview.rows.length || reconciliation.review.length > 0}
        onClick={() => void apply()}>{busy ? 'Importing…' : 'Approve Gmail statement import'}</button>
    </>}
    {error && <p role="alert">{error}</p>}{done && <p role="status">{done}</p>}
  </div>;
}
