import { useState } from 'react';
import { apiRequest } from './lib/api';

type Market = 'stock' | 'crypto';
type Action = 'buy' | 'sell';
type Holding = { ticker: string; market: Market; quantity: string; average_cost_sar: string };
type Portfolio = {
  starting_capital_sar: string; cash_sar: string; realised_pnl_sar: string;
  holdings: Holding[]; trading_halted: boolean;
};
type Result = {
  accepted: boolean; reason: string; before: Portfolio; after: Portfolio;
  simulated_notional_sar: string; estimated_fee_sar: string;
  real_orders_sent: number; live_trading_enabled: boolean; money_transferred: boolean;
};
const START: Portfolio = {
  starting_capital_sar: '500', cash_sar: '500',
  realised_pnl_sar: '0', holdings: [], trading_halted: false,
};

export function BrokerPaperTrading() {
  const [portfolio, setPortfolio] = useState<Portfolio>(START);
  const [market, setMarket] = useState<Market>('stock');
  const [ticker, setTicker] = useState('EXMPL');
  const [action, setAction] = useState<Action>('buy');
  const [quantity, setQuantity] = useState('1');
  const [price, setPrice] = useState('50');
  const [fee, setFee] = useState('0');
  const [source, setSource] = useState('Manually reviewed reference quote');
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function simulate() {
    setBusy(true); setError(''); setResult(null);
    try {
      const response = await apiRequest<Result>('/api/v1/finance/broker/paper/simulate', {
        method: 'POST',
        body: JSON.stringify({
          portfolio,
          quote: { ticker, market, currency: 'SAR', reference_price_sar: price, source },
          trade: { ticker, market, action, quantity },
          estimated_fee_sar: fee,
          daily_realised_pnl_sar: '0',
        }),
      });
      setResult(response);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Paper simulation failed.');
    } finally {
      setBusy(false);
    }
  }

  return <section className="surface cmPanel" aria-labelledby="broker-paper-title">
    <div className="sectionHeader">
      <h2 id="broker-paper-title">Crypto and stocks · SAR 500 paper trading</h2>
      <span className="statusBadge neutral">Simulation only</span>
    </div>
    <p className="muted">A research sandbox, not a brokerage connection. Quotes are entered manually in SAR. Never deposit money into ANUM; all live trading, borrowing and transfers are disabled.</p>
    <div className="finBrokerActions">
      <span><strong>Paper cash: </strong>{portfolio.cash_sar} SAR</span>
      <span><strong>Realised paper P&amp;L: </strong>{portfolio.realised_pnl_sar} SAR</span>
      <span><strong>Live orders: </strong>Disabled</span>
    </div>
    <div className="finBrokerStatement">
      <label>Market <select value={market} onChange={event => setMarket(event.target.value as Market)}>
        <option value="stock">Stocks</option><option value="crypto">Cryptocurrency</option>
      </select></label>
      <label>Ticker <input value={ticker} maxLength={25} onChange={event => setTicker(event.target.value.toUpperCase())}/></label>
      <label>Action <select value={action} onChange={event => setAction(event.target.value as Action)}>
        <option value="buy">Paper buy</option><option value="sell">Paper sell</option>
      </select></label>
      <label>Quantity <input type="number" min="0.0001" step="any" value={quantity} onChange={event => setQuantity(event.target.value)} /></label>
      <label>Reviewed price (SAR per unit) <input type="number" min="0.0001" step="any" value={price} onChange={event => setPrice(event.target.value)} /></label>
      <label>Estimated fee (SAR) <input type="number" min="0" step="any" value={fee} onChange={event => setFee(event.target.value)} /></label>
      <label>Price source <input value={source} maxLength={150} onChange={event => setSource(event.target.value)} /></label>
      <p className="muted">Per-order and per-asset exposure capped at SAR 100; no leverage. Paper risk stop at SAR 10 of daily losses. This is not investment advice or a forecast.</p>
      <div className="finBrokerActions">
        <button type="button" onClick={() => void simulate()} disabled={busy || portfolio.trading_halted}>
          {busy ? 'Calculating...' : 'Simulate trade'}
        </button>
        <button type="button" className="secondary" onClick={() => {
          setPortfolio(previous => ({ ...previous, trading_halted: true }));
          setResult(null);
        }} disabled={portfolio.trading_halted}>Stop paper trading</button>
        <button type="button" className="secondary" onClick={() => {
          setPortfolio({ ...START }); setResult(null); setError('');
        }}>Reset sandbox</button>
      </div>
    </div>
    {error && <p role="alert">{error}</p>}
    {result && <div aria-live="polite">
      <p role="status">{result.accepted ? 'Paper calculation accepted' : 'Paper calculation blocked'}: {result.reason}</p>
      <p>Notional: {result.simulated_notional_sar} SAR. No real orders or transfers occurred.</p>
      {result.accepted && <button type="button" className="secondary" onClick={() => {
        setPortfolio(result.after);
        setResult(null);
      }}>Apply to local paper portfolio</button>}
    </div>}
    <div className="cmTableScroll"><table>
      <caption>Local simulated positions (this browser session only)</caption>
      <thead><tr><th>Market</th><th>Symbol</th><th>Quantity</th><th>Average simulated cost (SAR)</th></tr></thead>
      <tbody>{portfolio.holdings.map(position =>
        <tr key={position.market + position.ticker}><td>{position.market}</td><td>{position.ticker}</td>
          <td>{position.quantity}</td><td>{position.average_cost_sar}</td></tr>)}
      {!portfolio.holdings.length && <tr><td colSpan={4}>No simulated holdings yet</td></tr>}</tbody>
    </table></div>
  </section>;
}
