import { useId, useRef, useState } from 'react';
import { apiRequest } from './lib/api';
import './FinanceBroker.css';

type Cash = { currency: string; settled: string; available: string };
type Position = { symbol: string; currency: string; quantity: string; side: string; last_price: string | null };
type Snapshot = { broker_id: string; account_id: string; as_of: string; source_reference: string; cash: Cash[]; positions: Position[] };
type Discrepancy = { kind: string; key: string; expected: string | null; actual: string | null; difference: string | null; currency: string | null };
type Reconciliation = { matches: boolean; stale: boolean; discrepancies: Discrepancy[]; currency_exposure: Record<string, string>; unpriced_positions: string[]; read_only: boolean; trades_executed: number; broker_id: string; account_id: string; baseline_time: string; observed_time: string; source_reference: string };
type Side = 'baseline' | 'observed';
type Status = 'empty' | 'loading' | 'ready';
type Slot = { csv: string; snapshot: Snapshot | null; status: Status; name: string };
const EMPTY: Slot = { csv: '', snapshot: null, status: 'empty', name: '' };
const root = '/api/v1/finance/broker';
const send = <T,>(endpoint: string, input: unknown): Promise<T> =>
  apiRequest<T>(`${root}${endpoint}`, { method: 'POST', body: JSON.stringify(input) });
const REQUIRED_HEADERS = 'row_type,broker_id,account_id,as_of,source_reference,currency,symbol,side,quantity,last_price,settled,available';
const MAX_FILE_BYTES = 512000;

function readableError(error: unknown) {
  return error instanceof Error ? error.message : 'The request could not be completed.';
}

function csvSafe(value: unknown): string {
  const text = String(value ?? '');
  const safe = /^[=+@\-\t\r]/.test(text) ? `'${text}` : text;
  return `"${safe.replaceAll('"', '""')}"`;
}

function reconciliationCsv(result: Reconciliation): string {
  const rows = [
    ['kind', 'key', 'expected', 'actual', 'difference', 'currency'],
    ...result.discrepancies.map(item => [
      item.kind, item.key, item.expected ?? '', item.actual ?? '', item.difference ?? '', item.currency ?? '',
    ]),
  ];
  return rows.map(row => row.map(csvSafe).join(',')).join('\r\n') + '\r\n';
}

function downloadReconciliation(result: Reconciliation) {
  const blob = new Blob([reconciliationCsv(result)], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = 'anum-broker-discrepancies.csv';
  anchor.click();
  URL.revokeObjectURL(url);
}

function StatementSummary({ snapshot, title }: { snapshot: Snapshot; title: string }) {
  return <div aria-label={title}>
    <p><strong>{title}</strong> <span className="statusBadge success">Validated format</span></p>
    <dl className="finIntelFacts">
      <div><dt>Broker</dt><dd>{snapshot.broker_id}</dd></div>
      <div><dt>Account</dt><dd>{snapshot.account_id}</dd></div>
      <div><dt>As of</dt><dd>{new Date(snapshot.as_of).toLocaleString()}</dd></div>
      <div><dt>Positions</dt><dd>{snapshot.positions.length}</dd></div>
      <div><dt>Cash currencies</dt><dd>{snapshot.cash.length}</dd></div>
    </dl>
    <div className="cmTableScroll">
      <table><caption>Cash available and settled, by currency</caption><thead>
        <tr><th>Currency</th><th>Settled</th><th>Available</th></tr>
      </thead><tbody>
        {snapshot.cash.map(cash => <tr key={cash.currency}><td>{cash.currency}</td><td>{cash.settled}</td><td>{cash.available}</td></tr>)}
      </tbody></table>
    </div>
  </div>;
}

export function BrokerStatementWorkbench() {
  const id = useId();
  const current = useRef(0);
  const [slots, setSlots] = useState<Record<Side, Slot>>({ baseline: EMPTY, observed: EMPTY });
  const [result, setResult] = useState<Reconciliation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);

  function change(side: Side, csv: string, name = '') {
    current.current++;
    setSlots(prev => ({ ...prev, [side]: { csv, name, snapshot: null, status: csv ? 'empty' : 'empty' } }));
    setResult(null);
    setError('');
  }

  async function upload(side: Side, file?: File) {
    if (!file) return;
    if (file.size > MAX_FILE_BYTES || !file.name.toLowerCase().endsWith('.csv')) {
      setError('Choose a UTF-8 CSV file smaller than 500 KB.');
      return;
    }
    const index = ++current.current;
    try {
      const text = await file.text();
      if (index !== current.current) return;
      change(side, text, file.name);
    } catch {
      setError('Could not read the selected CSV file.');
    }
  }

  async function preview(side: Side) {
    const csv = slots[side].csv;
    if (!csv.trim()) return;
    const index = ++current.current;
    setBusy(true); setError(''); setResult(null);
    setSlots(prev => ({ ...prev, [side]: { ...prev[side], status: 'loading' } }));
    try {
      const snapshot = await send<Snapshot>('/statement-preview', { statement: csv });
      if (index !== current.current) return;
      setSlots(prev => ({ ...prev, [side]: { ...prev[side], snapshot, status: 'ready' } }));
    } catch (err) {
      if (index !== current.current) return;
      setError(readableError(err));
      setSlots(prev => ({ ...prev, [side]: { ...prev[side], snapshot: null, status: 'empty' } }));
    } finally { if (index === current.current) setBusy(false); }
  }

  async function compare() {
    const baseline = slots.baseline.snapshot, observed = slots.observed.snapshot;
    if (!baseline || !observed) return;
    const index = ++current.current;
    setBusy(true); setError(''); setResult(null);
    try {
      const answer = await send<Reconciliation>('/reconcile', { baseline, observed });
      if (index === current.current) setResult(answer);
    } catch (err) {
      if (index === current.current) setError(readableError(err));
    } finally { if (index === current.current) setBusy(false); }
  }

  function clear() {
    current.current++;
    setBusy(false); setError(''); setResult(null);
    setSlots({ baseline: { ...EMPTY }, observed: { ...EMPTY } });
  }

  return <section className="surface cmPanel" aria-labelledby={id}>
    <div className="sectionHeader">
      <h2 id={id}>Broker statements and reconciliation</h2>
      <span className="statusBadge neutral">Read-only, no live trades</span>
    </div>
    <p className="muted">Review an account's cash and positions, compare two statement dates, and export differences. ANUM cannot accept deposits or execute broker orders. Files are sent to the ANUM API for in-memory validation, not saved as broker connections.</p>
    <button type="button" className="secondary" onClick={() => setOpen(!open)} aria-expanded={open}>
      {open ? 'Close statement review' : 'Open statement review'}
    </button>
    {open && <div>
      <p className="muted">Import CSV exports from your own brokerage account. Do not paste API tokens, passwords, or card details. These are user-supplied records, not verified live brokerage data.</p>
      <details><summary>CSV template and required fields</summary>
        <p className="muted">Every row must use the same broker ID, account ID, statement timestamp with timezone and source reference. Cash rows use settled and available; position rows use symbol, side, quantity and optional last_price.</p>
        <code style={{ overflowWrap: 'anywhere' }}>{REQUIRED_HEADERS}</code>
      </details>
      {(['baseline', 'observed'] as const).map(side => {
        const slot = slots[side];
        const title = side === 'baseline' ? 'Earlier statement' : 'Latest statement';
        return <div key={side} className="finBrokerStatement">
          <h3>{title}</h3>
          <label htmlFor={`${id}-file-${side}`}>Upload CSV document</label>
          <input id={`${id}-file-${side}`} type="file" accept=".csv,text/csv" disabled={busy} onChange={event => void upload(side, event.currentTarget.files?.[0])} />
          {slot.name && <p className="muted">Selected file: {slot.name}</p>}
          <label htmlFor={`${id}-csv-${side}`}>Or paste CSV text</label>
          <textarea id={`${id}-csv-${side}`} value={slot.csv} maxLength={MAX_FILE_BYTES} rows={4} onChange={event => change(side, event.target.value)} />
          <button type="button" className="secondary" disabled={busy || !slot.csv.trim()} onClick={() => void preview(side)}>
            {slot.status === 'loading' ? 'Validating...' : `Validate ${title.toLowerCase()}`}
          </button>
          {slot.snapshot && <StatementSummary snapshot={slot.snapshot} title={title} />}
        </div>;
      })}
      <div className="finBrokerActions">
        <button type="button" disabled={busy || !slots.baseline.snapshot || !slots.observed.snapshot} onClick={() => void compare()}>
          {busy ? 'Working...' : 'Compare statements'}
        </button>
        <button type="button" className="secondary" onClick={clear}>Clear statements</button>
      </div>
      {error && <p role="alert">{error}</p>}
      {result && <div aria-live="polite">
        <div className="sectionHeader"><h3>Reconciliation result</h3>
          <span className={`statusBadge ${result.matches ? 'success' : 'warning'}`}>
            {result.matches ? 'Matched' : `${result.discrepancies.length} differences`}
          </span>
        </div>
        <p><strong>Account:</strong> {result.broker_id} / {result.account_id}</p>
        <p><strong>Statement dates:</strong> {new Date(result.baseline_time).toLocaleDateString()} to {new Date(result.observed_time).toLocaleDateString()}</p>
        {result.stale && <p role="alert">The latest statement is stale. Do not treat balances or prices as current.</p>}
        {result.unpriced_positions.length > 0 && <p role="status">Missing prices for: {result.unpriced_positions.join(', ')}. Market values are incomplete.</p>}
        <p className="muted">No broker transaction was executed. Differences can reflect trades, transfers, fees or corporate actions; they must be checked against the broker.</p>
        <div className="cmTableScroll"><table>
          <caption>Changes between broker statements</caption>
          <thead><tr><th>Field</th><th>Account item</th><th>Earlier</th><th>Latest</th><th>Difference</th><th>Currency</th></tr></thead>
          <tbody>{result.discrepancies.length ? result.discrepancies.map((item, index) =>
            <tr key={`${item.kind}-${item.key}-${index}`}>
              <td>{item.kind.replaceAll('_', ' ')}</td><td>{item.key}</td>
              <td>{item.expected ?? 'Missing'}</td><td>{item.actual ?? 'Missing'}</td>
              <td>{item.difference ?? 'N/A'}</td><td>{item.currency ?? '-'}</td>
            </tr>) : <tr><td colSpan={6}>No cash or position quantity differences detected.</td></tr>}</tbody>
        </table></div>
        <h4>Position market values by currency</h4>
        <ul>{Object.entries(result.currency_exposure).map(([currency, amount]) => <li key={currency}>{currency}: {amount}</li>)}</ul>
        <button type="button" className="secondary" onClick={() => downloadReconciliation(result)}>Download differences CSV</button>
      </div>}
    </div>}
  </section>;
}
