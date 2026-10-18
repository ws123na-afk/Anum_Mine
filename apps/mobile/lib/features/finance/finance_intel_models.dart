import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'finance_models.dart';

/// The trading intelligence dashboard on the phone
/// (docs/finance-trading-intel.md#dashboard): read only and SIMULATED. It
/// reads `/api/v1/finance/intel/*`; the only change it can make is the
/// existing kill switch, which stops every simulated executor. Recorded text
/// (reasons, warnings) is shown as data and never followed.

/// The six lifecycle states, in order.
const strategyStates = [
  'DRAFT',
  'BACKTESTING',
  'VALIDATION',
  'PAPER_ACTIVE',
  'PAPER_SUSPENDED',
  'RETIRED'
];

/// Who can decide (intel/records.py `Agent`).
const intelAgents = [
  'trading_supervisor',
  'market_research',
  'technical_analysis',
  'strategy_research',
  'portfolio',
  'risk_guardian',
  'execution_coordinator',
  'performance_reviewer',
  'person'
];

Map<String, double?> _numbers(Map<String, Object?>? raw) => {
      for (final e in (raw ?? const <String, Object?>{}).entries)
        if (e.value == null || e.value is num)
          e.key: (e.value as num?)?.toDouble(),
    };

class IntelHolding {
  const IntelHolding(
      {required this.symbol,
      required this.currency,
      required this.quantity,
      this.marketValue,
      this.unrealised,
      this.priceAsOf});
  final String symbol, currency, quantity;
  final String? marketValue, unrealised, priceAsOf;

  factory IntelHolding.fromJson(JsonReader r) => IntelHolding(
        symbol: r.string('symbol'),
        currency: r.string('currency'),
        quantity: r.string('quantity'),
        marketValue: r.optString('market_value'),
        unrealised: r.optString('unrealised'),
        priceAsOf: r.optString('price_as_of'),
      );
}

class IntelPortfolio {
  const IntelPortfolio(
      {required this.id,
      required this.name,
      required this.mode,
      required this.cash,
      required this.realised,
      required this.unrealised,
      required this.fees,
      required this.slippage,
      required this.holdings,
      required this.holdingsTotal,
      required this.unpriced});
  final String id, name, mode;
  final List<CurrencyAmount> cash, realised, unrealised, fees, slippage;
  final List<IntelHolding> holdings;
  final int holdingsTotal;
  final List<String> unpriced;

  factory IntelPortfolio.fromJson(JsonReader r) => IntelPortfolio(
        id: r.string('id'),
        name: r.string('name'),
        mode: r.string('mode'),
        cash: r.optObjects('cash', CurrencyAmount.fromJson),
        realised: r.optObjects('realised', CurrencyAmount.fromJson),
        unrealised: r.optObjects('unrealised', CurrencyAmount.fromJson),
        fees: r.optObjects('fees', CurrencyAmount.fromJson),
        slippage: r.optObjects('slippage', CurrencyAmount.fromJson),
        holdings: r.optObjects('holdings', IntelHolding.fromJson),
        holdingsTotal: r.optInt('holdings_total') ?? 0,
        unpriced: r.optStrings('unpriced') ?? const [],
      );
}

/// The Guardian rules, read only: one plain line per rule that is set.
class IntelRule {
  const IntelRule(this.code, this.value);
  final String code, value;
}

List<IntelRule> _rules(JsonReader? r) {
  if (r == null) return const [];
  String amounts(String key) => formatAmounts(
      r.optObjects(key, (i) => CurrencyAmount.fromJson(i)).toList());
  final out = <IntelRule>[];
  final currencies = r.optStrings('allowed_currencies') ?? const [];
  if (currencies.isNotEmpty) {
    out.add(IntelRule('currencies', currencies.join(', ')));
  }
  for (final key in ['max_single', 'max_daily_total', 'approval_above']) {
    if ((r.optList(key) ?? const []).isNotEmpty) {
      out.add(IntelRule(key, amounts(key)));
    }
  }
  for (final key in ['blocked_categories', 'blocked_merchants']) {
    final list = r.optStrings(key) ?? const [];
    if (list.isNotEmpty) out.add(IntelRule(key, list.join(', ')));
  }
  for (final key in ['quiet_hours', 'cooling_off']) {
    if (r[key] != null) out.add(IntelRule(key, ''));
  }
  return out;
}

class IntelOverview {
  const IntelOverview(
      {required this.liveEnabled,
      required this.personEngaged,
      required this.globalEngaged,
      required this.portfolios,
      required this.riskVersion,
      required this.rules,
      required this.counts,
      required this.pendingRequests,
      required this.agentRequests});
  final bool liveEnabled, personEngaged, globalEngaged;
  final List<IntelPortfolio> portfolios;
  final int riskVersion, pendingRequests, agentRequests;
  final List<IntelRule> rules;
  final Map<String, int> counts;

  bool get stopped => personEngaged || globalEngaged;

  factory IntelOverview.fromJson(JsonReader r) {
    final simulation = r.optObject('simulation');
    final switches = r.optObject('switches');
    final risk = r.optObject('risk');
    return IntelOverview(
      liveEnabled: simulation?.optBool('live_enabled') ?? false,
      personEngaged: switches?.optBool('person_engaged') ?? false,
      globalEngaged: switches?.optBool('global_engaged') ?? false,
      portfolios: r.optObjects('portfolios', IntelPortfolio.fromJson),
      riskVersion: risk?.optInt('version') ?? 0,
      rules: _rules(risk?.optObject('rules')),
      counts: {
        for (final c in r.optObjects('strategies',
            (i) => MapEntry(i.string('state'), i.integer('count'))))
          c.key: c.value
      },
      pendingRequests: r.optInt('pending_requests') ?? 0,
      agentRequests: r.optInt('agent_requests') ?? 0,
    );
  }
}

class IntelStrategy {
  const IntelStrategy(
      {required this.id,
      required this.name,
      required this.version,
      required this.state,
      required this.symbols,
      required this.reason,
      required this.stateChangedBy,
      required this.stateChangedAt});
  final String id, name, state, reason, stateChangedBy;
  final int version;
  final List<String> symbols;
  final DateTime stateChangedAt;

  factory IntelStrategy.fromJson(JsonReader r) => IntelStrategy(
        id: r.string('id'),
        name: r.string('name'),
        version: r.optInt('version') ?? 1,
        state: r.string('state'),
        symbols: r.optStrings('symbols') ?? const [],
        reason: r.optString('reason') ?? '',
        stateChangedBy: r.optString('state_changed_by') ?? 'person',
        stateChangedAt: r.date('state_changed_at'),
      );
}

class IntelRun {
  const IntelRun(
      {required this.id,
      required this.kind,
      required this.verdict,
      required this.reproduced,
      required this.dataSource,
      required this.metrics,
      required this.benchmark,
      required this.reasons,
      required this.warnings,
      required this.folds,
      required this.createdAt});
  final String id, kind, verdict, dataSource;
  final bool reproduced;
  final Map<String, double?> metrics, benchmark;
  final List<String> reasons, warnings;
  final int folds;
  final DateTime createdAt;

  factory IntelRun.fromJson(JsonReader r) => IntelRun(
        id: r.string('id'),
        kind: r.string('kind'),
        verdict: r.string('verdict'),
        reproduced: r.optBool('reproduced') ?? false,
        dataSource: r.optString('data_source') ?? '',
        metrics: _numbers(r.optMap('metrics')),
        benchmark: _numbers(r.optMap('benchmark')),
        reasons: r.optStrings('reasons') ?? const [],
        warnings: r.optStrings('warnings') ?? const [],
        folds: (r.optList('folds') ?? const []).length,
        createdAt: r.date('created_at'),
      );
}

class IntelDecision {
  const IntelDecision(
      {required this.id,
      required this.agent,
      required this.action,
      required this.outcome,
      required this.reasons,
      required this.createdAt});
  final String id, agent, action, outcome;
  final List<String> reasons;
  final DateTime createdAt;

  factory IntelDecision.fromJson(JsonReader r) => IntelDecision(
        id: r.string('id'),
        agent: r.string('agent'),
        action: r.string('action'),
        outcome: r.string('outcome'),
        reasons: r.optStrings('reasons') ?? const [],
        createdAt: r.date('created_at'),
      );
}

class IntelPage<T> {
  const IntelPage(this.items, this.total, this.offset);
  final List<T> items;
  final int total, offset;

  static IntelPage<T> read<T>(JsonReader r, T Function(JsonReader item) item) =>
      IntelPage(r.optObjects('items', item), r.optInt('total') ?? 0,
          r.optInt('offset') ?? 0);
}

/// One day of a portfolio's current run in one currency. A day that is not
/// [complete] is a gap: something held had no fresh recorded price.
class EquityPoint {
  const EquityPoint(
      {required this.day,
      required this.complete,
      this.value,
      this.pnl,
      this.drawdown,
      this.drawdownPct});
  final String day;
  final bool complete;
  final String? value, pnl, drawdown;
  final double? drawdownPct;

  factory EquityPoint.fromJson(JsonReader r) => EquityPoint(
        day: r.string('day'),
        complete: r.optBool('complete') ?? false,
        value: r.optString('value'),
        pnl: r.optString('pnl'),
        drawdown: r.optString('drawdown'),
        drawdownPct: r.optNumber('drawdown_pct'),
      );
}

class EquitySeries {
  const EquitySeries(
      {required this.currency,
      required this.points,
      required this.gaps,
      this.maxDrawdown,
      this.maxDrawdownPct,
      this.peakDay,
      this.troughDay});
  final String currency;
  final List<EquityPoint> points;
  final int gaps;
  final String? maxDrawdown, peakDay, troughDay;
  final double? maxDrawdownPct;

  EquityPoint? get latest {
    for (final p in points.reversed) {
      if (p.complete) return p;
    }
    return null;
  }

  factory EquitySeries.fromJson(JsonReader r) => EquitySeries(
        currency: r.string('currency'),
        points: r.optObjects('points', EquityPoint.fromJson),
        gaps: r.optInt('gaps') ?? 0,
        maxDrawdown: r.optString('max_drawdown'),
        maxDrawdownPct: r.optNumber('max_drawdown_pct'),
        peakDay: r.optString('peak_day'),
        troughDay: r.optString('trough_day'),
      );
}

class EquityCurve {
  const EquityCurve(
      {required this.start,
      required this.end,
      required this.orders,
      required this.series});
  final String start, end;
  final int orders;
  final List<EquitySeries> series;

  factory EquityCurve.fromJson(JsonReader r) => EquityCurve(
        start: r.string('start'),
        end: r.string('end'),
        orders: r.optInt('orders') ?? 0,
        series: r.optObjects('series', EquitySeries.fromJson),
      );
}

abstract interface class FinanceIntelRepository {
  Future<IntelOverview> overview();
  Future<IntelPage<IntelStrategy>> strategies(
      {String? state, required int offset, required int limit});
  Future<IntelPage<IntelRun>> runs(String strategyId,
      {required int offset, required int limit});
  Future<IntelPage<IntelDecision>> decisions(
      {String? agent, required int offset, required int limit});
  Future<EquityCurve> equity(String portfolioId, {required int days});

  /// The person's kill switch: true stops every paper and shadow executor.
  Future<void> setKillSwitch(bool engaged);
}

class ApiFinanceIntelRepository implements FinanceIntelRepository {
  ApiFinanceIntelRepository(this.api);
  final AnumApiClient api;
  static const base = '/api/v1/finance';

  String _q(Map<String, Object?> values) {
    final kept = {
      for (final e in values.entries)
        if (e.value != null && '${e.value}'.isNotEmpty) e.key: '${e.value}'
    };
    return kept.isEmpty ? '' : '?${Uri(queryParameters: kept).query}';
  }

  Future<JsonReader> _get(String path) async =>
      JsonReader.of(await api.request('GET', '$base$path'));

  @override
  Future<IntelOverview> overview() async =>
      IntelOverview.fromJson(await _get('/intel/overview'));

  @override
  Future<IntelPage<IntelStrategy>> strategies(
          {String? state, required int offset, required int limit}) async =>
      IntelPage.read(
          await _get('/intel/strategies${_q({
                'state': state,
                'limit': limit,
                'offset': offset
              })}'),
          IntelStrategy.fromJson);

  @override
  Future<IntelPage<IntelRun>> runs(String strategyId,
          {required int offset, required int limit}) async =>
      IntelPage.read(
          await _get(
              '/intel/strategies/${Uri.encodeComponent(strategyId)}/runs${_q({
                'limit': limit,
                'offset': offset
              })}'),
          IntelRun.fromJson);

  @override
  Future<IntelPage<IntelDecision>> decisions(
          {String? agent, required int offset, required int limit}) async =>
      IntelPage.read(
          await _get('/intel/decisions${_q({
                'agent': agent,
                'limit': limit,
                'offset': offset
              })}'),
          IntelDecision.fromJson);

  @override
  Future<EquityCurve> equity(String portfolioId, {required int days}) async =>
      EquityCurve.fromJson(await _get(
          '/intel/portfolios/${Uri.encodeComponent(portfolioId)}/equity${_q({
            'days': days
          })}'));

  @override
  Future<void> setKillSwitch(bool engaged) async {
    await api.request('PUT', '$base/execution/kill-switch', body: {
      'scope': 'person',
      'engaged': engaged,
      if (engaged) 'reason': 'Stopped from the trading dashboard on the phone.',
    });
  }
}
