import { useState } from 'react';
import { apiRequest } from './lib/api';

type Cash = { currency: string; settled: string; available: string };
type Position = { symbol: string; currency: string; quantity: string; side: string; last_price: string | null };
type Snapshot = { broker_id: string; account_id: string; as_of: string; source_reference: string; cash: Cash[]; positions: Position[] };
type Discrepancy = { kind: string; key: string; expected: string | null; actual: string | null; difference: string | null; currency: string | null };
type Reconciliation = { matches: boolean; stale: boolean; discrepancies: Discrepancy[]; currency_exposure: Record<string, string>; unpriced_positions: string[]; read_only: boolean; trades_executed: number };

const root = '/api/v1/finance/broker';
const send = <T,>(endpoint: string, input: unknown): Promise<T> =>
  apiRequest<T>(`${root}${endpoint}`, { method: 'POST', body: JSON.stringify(input) });

export function BrokerStatementWorkbench() {
  const [baselineCsv, setBaselineCsv] = useState('');
  const [observedCsv, setObservedCsv] = useState('');
  const [baseline, setBaseline] = useState<Snapshot | null>(null);
  const [observed, setObserved] = useState<Snapshot | null>(null);
  const [result, setResult] = useState<Reconciliation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [show, setShow] = useState(false);

  async function preview(which: 'baseline' | 'observed') {
    setBusy(true); setError(''); setResult(null);
    try {
      const snapshot = await send<Snapshot>('/statement-preview', {
        statement: which === 'baseline' ? baselineCsv : observedCsv,
      });
      if (which === 'baseline') setBaseline(snapshot);
      else setObserved(snapshot);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not validate statement');
      if (which === 'baseline') setBaseline(null);
      else setObserved(null);
    } finally { setBusy(false); }
  }

  async function compare() {
    if (!baseline || !observed) return;
    setBusy(true); setError('');
    try {
      setResult(await send<Reconciliation>('/reconcile', { baseline, observed }));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reconcile statements');
      setResult(null);
    } finally { setBusy(false); }
  }

  return <section className="surface cmPanel" aria-labelledby="broker-statement-title">
    <div className="sectionHeader">
      <h2 id="broker-statement-title">Broker statement review</h2>
      <span className="statusBadge neutral">Read-only</span>
    </div>
    <p className="muted">Compare two broker statement CSV exports before connecting a brokerage account. ANUM does not receive deposits, connect to the broker, or execute live trades here. Documents are validated in memory and are not saved by this feature.</p>
    <button type="button" className="secondary" onClick={() => setShow(!show)} aria-expanded={show}>
      {show ? 'Close statement review' : 'Open statement review'}
    </button>
    {show && <>
      <p className="muted">Required CSV columns: row_type, broker_id, account_id, as_of, source_reference, currency, symbol, side, quantity, last_price, settled, available. Use an ISO timestamp with a timezone. Do not enter passwords or API keys.</p>
      <div className="finBrokerInputs">
        <label>Earlier broker statement
          <textarea value={baselineCsv} maxLength={512000} rows={5} onChange={e => { setBaselineCsv(e.target.value); setBaseline(null); setResult(null); }} />
        </label>
        <button type="button" className="secondary" disabled={busy || !baselineCsv.trim()} onClick={() => void preview('baseline')}>Validate earlier statement</button>
        {baseline && <p role="status">Verified format: {baseline.positions.length} position rows, {baseline.cash.length} cash currencies.</p>}
        <label>Latest broker statement
          <textarea value={observedCsv} maxLength={512000} rows={5} onChange={e => { setObservedCsv(e.target.value); setObserved(null); setResult(null); }} />
        </label>
        <button type="button" className="secondary" disabled={busy || !observedCsv.trim()} onClick={() => void preview('observed')}>Validate latest statement</button>
        {observed && <p role="status">Verified format: {observed.positions.length} position rows, {observed.cash.length} cash currencies.</p>}
      </div>
      <button type="button" disabled={busy || !baseline || !observed} onClick={() => void compare()}>
        Compare statements
      </button>
      {error && <p role="alert">{error}</p>}
      {result && <div aria-live="polite">
        <p><strong>{result.matches ? 'Balances and positions match' : 'Differences detected'}</strong></p>
        {result.stale && <p role="alert">Latest statement timestamp is stale. Do not use it to make current trading decisions.</p>}
        <p>Trades executed by this feature: {result.trades_executed}. {result.unpriced_positions.length} positions have no recorded price.</p>
        {result.discrepancies.length > 0 && <div className="cmTableScroll"><table>
          <thead><tr><th>Difference</th><th>Account item</th><th>Earlier</th><th>Latest</th><th>Currency</th></tr></thead>
          <tbody>{result.discrepancies.map((item, index) => <tr key={index}>
            <td>{item.kind.replaceAll('_', ' ')}</td><td>{item.key}</td>
            <td>{item.expected ?? 'Missing'}</td><td>{item.actual ?? 'Missing'}</td><td>{item.currency ?? '-'}</td>
          </tr>)}</tbody>
        </table></div>}
        <p className="muted">Values are calculated from the supplied snapshots, not verified directly with a broker. Currency exposures are displayed separately.</p>
        <ul>{Object.entries(result.currency_exposure).map(([currency, amount]) => <li key={currency}>{currency}: {amount}</li>)}</ul>
      </div>}
    </>}
  </section>;
}
