// A recorded-looking SIMULATED equity history for the mocked API (docs/finance-trading-intel.md#pl-and-drawdown-over-time).
// Plainly fake, deterministic numbers: a paper run that started 40 days before 2026-10-15, rose, fell
// and recovered, with one gap day where the held symbol had no fresh recorded price.
export interface EquityOptions { mode?: 'paper' | 'shadow'; currencies?: string[]; orders?: number; runDays?: number }

const END = Date.UTC(2026, 9, 15);
const DAY = 86_400_000;

export function equityBody(portfolioId: string, days: number, { mode = 'paper', currencies = ['USD'], orders = 3, runDays = 40 }: EquityOptions = {}) {
  const count = Math.max(1, Math.min(days, runDays));
  const series = currencies.map((currency, c) => {
    let peak = -Infinity, worst = 0, worstPct: number | null = mode === 'paper' ? 0 : null, peakDay: string | null = null, troughDay: string | null = null, peakAt = '';
    const points = Array.from({ length: count }, (_, i) => {
      const day = new Date(END - (count - 1 - i) * DAY).toISOString().slice(0, 10);
      const k = runDays - count + i;
      if (k === 25) return { day, value: null, pnl: null, drawdown: null, drawdown_pct: null, complete: false };
      const pnl = (k < 15 ? k * 20 : k < 25 ? 300 - (k - 15) * 45 : -150 + (k - 25) * 30) * (c + 1) - 1;
      const value = 10000 * (c + 1) + pnl;
      const basis = mode === 'paper' ? value : pnl;
      if (basis > peak) { peak = basis; peakAt = day; }
      const drawdown = basis - peak;
      const pct = mode === 'paper' ? drawdown / peak : null;
      if (drawdown < worst) { worst = drawdown; worstPct = pct; peakDay = peakAt; troughDay = day; }
      return { day, value: mode === 'paper' ? value.toFixed(2) : null, pnl: pnl.toFixed(2), drawdown: drawdown.toFixed(2), drawdown_pct: pct, complete: true };
    });
    const gaps = points.filter((p) => !p.complete).length;
    return { currency, points, max_drawdown: worst.toFixed(2), max_drawdown_pct: worstPct, peak_day: peakDay, trough_day: troughDay, gaps, unpriced: gaps ? ['EXMPL'] : [] };
  });
  return {
    portfolio_id: portfolioId, name: 'Practice', mode, start: series[0]?.points[0]?.day ?? '2026-10-15', end: '2026-10-15', days: count, orders,
    max_price_age_days: 3, prices_truncated: false, series: orders ? series : series.slice(0, 1), note: 'SIMULATED.', generated_at: '2026-10-15T08:00:00Z', simulated: true,
  };
}
