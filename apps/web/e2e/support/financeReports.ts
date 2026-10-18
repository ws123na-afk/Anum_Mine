// Recorded-looking post-close daily trading reports for the mocked API (docs/finance-trading-intel.md#daily-report).
// Plainly fake, deterministic values: an example symbol, round prices, agent reasons as plain words.
const summary = (i: number) => ({ active_strategies: 1, proposed_orders: i % 3 === 0 ? 1 : 0, refused_signals: i % 4 === 1 ? 1 : 0, suspended: i === 2 ? 1 : 0,
  waiting_for_approval: i === 0 ? 1 : 0, problems: i === 2 ? 1 : 0, market_closed: 0 });

export function reportList(count: number) {
  return Array.from({ length: count }, (_, i) => {
    const date = new Date(Date.UTC(2026, 9, 15) - i * 86_400_000).toISOString().slice(0, 10);
    return { id: `fin_dec_report_${i}`, date, created_at: `${date}T14:00:00Z`, summary: summary(i), simulated: true };
  });
}

export function reportDetail(id: string) {
  const i = Number(id.split('_').pop() ?? 0) || 0;
  const [item] = reportList(i + 1).slice(i);
  const s = summary(i);
  const report = {
    label: 'SIMULATED', disclaimer: 'SIMULATED. Paper trading with recorded or imported data and stated costs; no real order was placed.', phase: 'post_close',
    date: item.date, generated_at: item.created_at, summary: s,
    data_health: [{ symbol: 'EXMPL', venue: 'XNYS', last_bar: item.date, sessions_behind: 0, source: 'csv_import', status: 'ok', reasons: [] }],
    orders: s.proposed_orders ? [{ strategy: 'Trend example v2', symbol: 'EXMPL', side: 'buy', quantity: '10', target_weight: 0.2, estimate: { fill_price: '50.00', price_source: 'bar close', fee: '1.00' }, guardian: 'allowed', intent_id: 'fin_int_example' }] : [],
    refused: s.refused_signals ? [{ strategy: 'Trend example v2', agent: 'risk_guardian', action: 'check', reasons: ['data_stale'] }] : [],
    suspended: s.suspended ? [{ strategy: 'Trend example v2', agent: 'performance_reviewer', reasons: ['drawdown_beyond_validation'], why: 'Drawdown beyond what validation showed' }] : [],
    portfolios: [{ strategy: 'Trend example v2', cash: [{ currency: 'USD', amount: '9500.00' }], realised: [], unrealised: [{ currency: 'USD', amount: '25.00' }], fees: [{ currency: 'USD', amount: '1.00' }], unpriced: [] }],
    performance: [{ strategy: 'Trend example v2', since: '2026-09-01', return: 0.012, benchmark_return: 0.018, max_drawdown: -0.031, flags: ['behind_benchmark'] }],
    problems: s.problems ? [{ agent: 'performance_reviewer', action: 'review', outcome: 'degrading', reasons: ['behind_benchmark'], strategy: 'Trend example v2' }] : [],
    next_steps: s.waiting_for_approval ? ['Review the 1 SIMULATED order request(s) waiting for your approval under Finance, Paper trading.'] : ['Nothing needs your action today.'],
  };
  const commentary = i === 0
    ? { status: 'ok', reason: null, text: 'Simulated results only. One order waits for your approval; data for EXMPL is current.', model: 'anthropic/example-model', label: 'Model commentary', created_at: item.created_at }
    : { status: 'skipped', reason: 'no_model', text: null, model: null, label: 'Model commentary', created_at: item.created_at };
  return { ...item, report, markdown: `# Trading report, ${item.date} (after the close, SIMULATED)\n`, commentary };
}
