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
  'RETIRED',
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
  'person',
];

Map<String, double?> _numbers(Map<String, Object?>? raw) => {
      for (final e in (raw ?? const <String, Object?>{}).entries)
        if (e.value == null || e.value is num)
          e.key: (e.value as num?)?.toDouble(),
    };

class IntelHolding {
  const IntelHolding({
    required this.symbol,
    required this.currency,
    required this.quantity,
    this.marketValue,
    this.unrealised,
    this.priceAsOf,
  });
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
  const IntelPortfolio({
    required this.id,
    required this.name,
    required this.mode,
    required this.cash,
    required this.realised,
    required this.unrealised,
    required this.fees,
    required this.slippage,
    required this.holdings,
    required this.holdingsTotal,
    required this.unpriced,
  });
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
        r.optObjects(key, (i) => CurrencyAmount.fromJson(i)).toList(),
      );
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
  const IntelOverview({
    required this.liveEnabled,
    required this.personEngaged,
    required this.globalEngaged,
    required this.portfolios,
    required this.riskVersion,
    required this.rules,
    required this.counts,
    required this.pendingRequests,
    required this.agentRequests,
    this.venues = const [],
  });
  final List<IntelVenue> venues;
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
        for (final c in r.optObjects(
          'strategies',
          (i) => MapEntry(i.string('state'), i.integer('count')),
        ))
          c.key: c.value,
      },
      pendingRequests: r.optInt('pending_requests') ?? 0,
      agentRequests: r.optInt('agent_requests') ?? 0,
      venues: r.optObjects('venues', IntelVenue.fromJson),
    );
  }
}

/// A venue's regular session now (intel/markets.py): `open`, `closed` or
/// `holiday`, in its own time zone. Where holidays are announced each year
/// ([holidaysKnown] false) an open session may in fact be closed.
class IntelVenue {
  const IntelVenue({
    required this.code,
    required this.name,
    required this.timezone,
    required this.state,
    required this.localTime,
    required this.holidaysKnown,
    required this.reason,
  });
  final String code, name, timezone, state, localTime, reason;
  final bool holidaysKnown;

  /// Weekday index 0 (Monday) to 6 of the venue's own date, read from the
  /// offset the API sent (never converted to the phone's time zone).
  int? get weekday {
    final m = RegExp(r'^(\d{4})-(\d{2})-(\d{2})').firstMatch(localTime);
    if (m == null) return null;
    return DateTime.utc(
          int.parse(m[1]!),
          int.parse(m[2]!),
          int.parse(m[3]!),
        ).weekday -
        1;
  }

  String get hhmm {
    final m = RegExp(r'T(\d{2}):(\d{2})').firstMatch(localTime);
    return m == null ? '' : '${m[1]}:${m[2]}';
  }

  factory IntelVenue.fromJson(JsonReader r) => IntelVenue(
        code: r.string('code'),
        name: r.optString('name') ?? r.string('code'),
        timezone: r.optString('timezone') ?? '',
        state: r.optString('state') ?? 'closed',
        localTime: r.optString('local_time') ?? '',
        holidaysKnown: r.optBool('holidays_known') ?? false,
        reason: r.optString('reason') ?? '',
      );
}

class IntelStrategy {
  const IntelStrategy({
    required this.id,
    required this.name,
    required this.version,
    required this.state,
    required this.symbols,
    required this.reason,
    required this.stateChangedBy,
    required this.stateChangedAt,
    this.portfolioId,
  });
  final String id, name, state, reason, stateChangedBy;

  /// The paper portfolio this version trades in, once chosen.
  final String? portfolioId;
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
        portfolioId: r.optString('portfolio_id'),
      );
}

class IntelRun {
  const IntelRun({
    required this.id,
    required this.kind,
    required this.verdict,
    required this.reproduced,
    required this.dataSource,
    required this.metrics,
    required this.benchmark,
    required this.reasons,
    required this.warnings,
    required this.folds,
    required this.createdAt,
  });
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
  const IntelDecision({
    required this.id,
    required this.agent,
    required this.action,
    required this.outcome,
    required this.reasons,
    required this.createdAt,
  });
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
      IntelPage(
        r.optObjects('items', item),
        r.optInt('total') ?? 0,
        r.optInt('offset') ?? 0,
      );
}

/// One day of a portfolio's current run in one currency. A day that is not
/// [complete] is a gap: something held had no fresh recorded price.
class EquityPoint {
  const EquityPoint({
    required this.day,
    required this.complete,
    this.value,
    this.pnl,
    this.drawdown,
    this.drawdownPct,
  });
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
  const EquitySeries({
    required this.currency,
    required this.points,
    required this.gaps,
    this.maxDrawdown,
    this.maxDrawdownPct,
    this.peakDay,
    this.troughDay,
    this.benchmark = const [],
    this.benchmarkReason,
    this.benchmarkSymbols = const [],
    this.benchmarkStart,
  });
  final String currency;
  final List<EquityPoint> points;

  /// Buy and hold of the same symbols, P&L per point (null: none that day).
  /// Empty when there is no line; [benchmarkReason] then says why.
  final List<double?> benchmark;
  final String? benchmarkReason, benchmarkStart;
  final List<String> benchmarkSymbols;

  bool get hasBenchmark => benchmark.any((v) => v != null);

  double? get latestBenchmark {
    for (final v in benchmark.reversed) {
      if (v != null) return v;
    }
    return null;
  }

  final int gaps;
  final String? maxDrawdown, peakDay, troughDay;
  final double? maxDrawdownPct;

  EquityPoint? get latest {
    for (final p in points.reversed) {
      if (p.complete) return p;
    }
    return null;
  }

  factory EquitySeries.fromJson(JsonReader r) {
    final points = r.optObjects('points', EquityPoint.fromJson);
    final bench = r.optObject('benchmark');
    final byDay = <String, double?>{
      for (final b in bench?.optObjects(
            'points',
            (i) => MapEntry(
              i.string('day'),
              double.tryParse(i.optString('pnl') ?? ''),
            ),
          ) ??
          const <MapEntry<String, double?>>[])
        b.key: b.value,
    };
    final reason = bench?.optString('reason');
    return EquitySeries._read(
      r,
      points,
      benchmark: bench == null || reason != null
          ? const []
          : [for (final p in points) byDay[p.day]],
      benchmarkReason: reason,
      benchmarkSymbols: bench?.optStrings('symbols') ?? const [],
      benchmarkStart: bench?.optString('start'),
    );
  }

  factory EquitySeries._read(
    JsonReader r,
    List<EquityPoint> points, {
    required List<double?> benchmark,
    String? benchmarkReason,
    List<String> benchmarkSymbols = const [],
    String? benchmarkStart,
  }) =>
      EquitySeries(
        currency: r.string('currency'),
        points: points,
        benchmark: benchmark,
        benchmarkReason: benchmarkReason,
        benchmarkSymbols: benchmarkSymbols,
        benchmarkStart: benchmarkStart,
        gaps: r.optInt('gaps') ?? 0,
        maxDrawdown: r.optString('max_drawdown'),
        maxDrawdownPct: r.optNumber('max_drawdown_pct'),
        peakDay: r.optString('peak_day'),
        troughDay: r.optString('trough_day'),
      );
}

class EquityCurve {
  const EquityCurve({
    required this.start,
    required this.end,
    required this.orders,
    required this.series,
  });
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

/// A strategy rule the person may start from (`GET /intel/rules`).
class StrategyRule {
  const StrategyRule({
    required this.key,
    required this.name,
    required this.candidate,
    required this.candidateName,
    required this.hypothesis,
    required this.entry,
    required this.exit,
    required this.minSymbols,
    required this.maxSymbols,
  });
  final String key, name, candidateName, hypothesis, entry, exit;
  final int candidate, minSymbols, maxSymbols;

  factory StrategyRule.fromJson(JsonReader r) => StrategyRule(
        key: r.string('key'),
        name: r.optString('name') ?? r.string('key'),
        candidate: r.optInt('candidate') ?? 0,
        candidateName: r.optString('candidate_name') ?? '',
        hypothesis: r.optString('hypothesis') ?? '',
        entry: r.optString('entry') ?? '',
        exit: r.optString('exit') ?? '',
        minSymbols: r.optInt('min_symbols') ?? 1,
        maxSymbols: r.optInt('max_symbols') ?? 1,
      );
}

/// An instrument with its stored daily bars (`GET /intel/instruments`).
class IntelInstrument {
  const IntelInstrument(
      {required this.id,
      required this.symbol,
      required this.bars,
      this.firstBar,
      this.lastBar,
      this.assetClass = '',
      this.venue = '',
      this.currency = '',
      this.lastSource});
  final String id, symbol, assetClass, venue, currency;
  final int bars;
  final String? firstBar, lastBar, lastSource;

  factory IntelInstrument.fromJson(JsonReader r) => IntelInstrument(
        id: r.string('id'),
        symbol: r.string('symbol'),
        bars: r.optInt('bars') ?? 0,
        firstBar: r.optString('first_bar'),
        lastBar: r.optString('last_bar'),
        assetClass: r.optString('asset_class') ?? '',
        venue: r.optString('venue') ?? '',
        currency: r.optString('currency') ?? '',
        lastSource: r.optString('last_source'),
      );
}

/// The asset classes an instrument may have (intel/marketdata.py `AssetClass`).
const intelAssetClasses = ['equity', 'etf', 'crypto', 'forex', 'commodity'];

/// The largest bars file the API accepts (`BarsInput.payload`).
const maxBarsFileBytes = 2000000;

/// What one bars import did (`POST /intel/instruments/{id}/bars`).
class BarsImport {
  const BarsImport(
      {required this.imported,
      required this.changed,
      required this.issues,
      this.first,
      this.last});
  final int imported, changed;
  final List<String> issues;
  final String? first, last;

  factory BarsImport.fromJson(JsonReader r) => BarsImport(
        imported: r.optInt('imported') ?? 0,
        changed: r.optInt('changed') ?? 0,
        first: r.optString('first'),
        last: r.optString('last'),
        issues: r.optObjects(
            'issues',
            (i) =>
                '${i.optString('day') ?? ''} ${i.optString('message') ?? i.optString('code') ?? ''}'
                    .trim()),
      );
}

/// A bars file picked on the phone: its name and text, or why it cannot be sent.
typedef BarsFile = ({String name, String? text, String? problem});

/// Picks one bars file (CSV) on the phone; null when the person cancels.
typedef BarsFilePicker = Future<BarsFile?> Function();

/// The Supervisor phases, in the order of a trading day and beyond.
const intelPhases = ['pre_open', 'intraday', 'post_close', 'weekly', 'monthly'];

/// The moves the person may make from each state (intel/lifecycle.py); the
/// server checks again and its refusal is shown as is.
const strategyMoves = <String, List<String>>{
  'DRAFT': ['BACKTESTING', 'RETIRED'],
  'BACKTESTING': ['VALIDATION', 'DRAFT', 'RETIRED'],
  'VALIDATION': ['PAPER_ACTIVE', 'BACKTESTING', 'RETIRED'],
  'PAPER_ACTIVE': ['PAPER_SUSPENDED', 'RETIRED'],
  'PAPER_SUSPENDED': ['PAPER_ACTIVE', 'RETIRED'],
  'RETIRED': [],
};

/// Starting paper trading or retiring asks for a stronger confirmation.
bool isWeightyMove(String target) =>
    target == 'PAPER_ACTIVE' || target == 'RETIRED';

/// What one Supervisor cycle did (`POST /intel/supervisor/run`). SIMULATED.
class IntelCycle {
  const IntelCycle({
    required this.phase,
    required this.skipped,
    required this.active,
    required this.proposed,
    required this.refused,
    required this.suspended,
    required this.halted,
    this.reportId,
  });
  final String phase;
  final bool skipped;
  final int active, proposed, refused, suspended, halted;
  final String? reportId;

  factory IntelCycle.fromJson(JsonReader r) => IntelCycle(
        phase: r.optString('phase') ?? '',
        skipped: r.optBool('skipped') ?? false,
        active: r.optInt('active_strategies') ?? 0,
        proposed: r.optInt('proposed_orders') ?? 0,
        refused: r.optInt('refused_signals') ?? 0,
        suspended: r.optInt('suspended') ?? 0,
        halted: r.optInt('halted') ?? 0,
        reportId: r.optString('report_id'),
      );
}

/// The summary figures of a daily report, in a fixed order.
const reportSummaryKeys = [
  'active_strategies',
  'proposed_orders',
  'waiting_for_approval',
  'refused_signals',
  'suspended',
  'problems',
  'market_closed',
];

/// One post-close daily report in the list (`GET /intel/daily-reports`).
class DailyReportItem {
  const DailyReportItem({
    required this.id,
    required this.date,
    required this.createdAt,
    required this.summary,
  });
  final String id, date;
  final DateTime createdAt;
  final Map<String, int> summary;

  int count(String key) => summary[key] ?? 0;

  /// Orders wait, a strategy was suspended or a problem was recorded.
  bool get needsAction =>
      count('waiting_for_approval') > 0 ||
      count('suspended') > 0 ||
      count('problems') > 0;

  static Map<String, int> _summary(JsonReader? r) => {
        for (final key in reportSummaryKeys) key: r?.optInt(key) ?? 0,
      };

  factory DailyReportItem.fromJson(JsonReader r) => DailyReportItem(
        id: r.string('id'),
        date: r.optString('date') ?? '',
        createdAt: r.date('created_at'),
        summary: _summary(r.optObject('summary')),
      );
}

/// One entry of a report section: a heading, labelled fields (the labels
/// are keys the view translates) and recorded reasons. Recorded text is data
/// to show, never instructions.
class ReportLine {
  const ReportLine(this.title, this.fields, [this.reasons = const []]);
  final String title;
  final List<MapEntry<String, String>> fields;
  final List<String> reasons;
}

String _pct(double? v) =>
    v == null ? '-' : '${v > 0 ? '+' : ''}${(v * 100).toStringAsFixed(1)}%';

String _amounts(List<JsonReader> rows) => rows.isEmpty
    ? '-'
    : rows
        .map(
          (a) =>
              '${a.optString('currency') ?? ''} ${a.optString('amount') ?? ''}',
        )
        .join(', ');

String _raw(Object? v) => v == null ? '-' : '$v';

/// A whole daily report (`GET /intel/daily-reports/{id}`), its sections as
/// lines the phone shows as text.
class DailyReport {
  const DailyReport({
    required this.item,
    required this.disclaimer,
    required this.nextSteps,
    required this.orders,
    required this.refused,
    required this.suspended,
    required this.data,
    required this.portfolios,
    required this.performance,
    required this.problems,
    required this.markdown,
    this.commentaryStatus,
    this.commentaryReason,
    this.commentaryText,
    this.commentaryModel,
  });
  final DailyReportItem item;
  final String disclaimer, markdown;

  /// Model commentary (docs/finance-trading-intel.md#model-commentary):
  /// `ok` with text, or `skipped`, `withheld`, `failed` with a reason.
  final String? commentaryStatus,
      commentaryReason,
      commentaryText,
      commentaryModel;
  final List<String> nextSteps;
  final List<ReportLine> orders,
      refused,
      suspended,
      data,
      portfolios,
      performance,
      problems;

  factory DailyReport.fromJson(JsonReader r) {
    final body = r.optObject('report');
    List<ReportLine> lines(
      String key,
      ReportLine Function(JsonReader i) read,
    ) =>
        body?.optObjects(key, read) ?? const [];
    MapEntry<String, String> f(String k, String v) => MapEntry(k, v);
    return DailyReport(
      item: DailyReportItem(
        id: r.string('id'),
        date: r.optString('date') ?? '',
        createdAt: r.date('created_at'),
        summary: DailyReportItem._summary(body?.optObject('summary')),
      ),
      disclaimer: body?.optString('disclaimer') ?? '',
      nextSteps: body?.optStrings('next_steps') ?? const [],
      orders: lines('orders', (o) {
        final est = o.optObject('estimate');
        return ReportLine(o.optString('strategy') ?? '-', [
          f(
            'order',
            '${_raw(o.optString('side'))} ${_raw(o['quantity'])} ${_raw(o.optString('symbol'))}',
          ),
          f('target', _pct(o.optNumber('target_weight'))),
          f(
            'fill',
            '${est?.optString('fill_price') ?? '-'} (${est?.optString('price_source') ?? '-'})',
          ),
          f('fee', est?.optString('fee') ?? '-'),
          f('guardian', o.optString('guardian') ?? '-'),
        ]);
      }),
      refused: lines(
        'refused',
        (x) => ReportLine(
            x.optString('strategy') ?? '-',
            [
              f('agent', x.optString('agent') ?? '-'),
            ],
            x.optStrings('reasons') ?? const []),
      ),
      suspended: lines(
        'suspended',
        (x) => ReportLine(
          x.optString('strategy') ?? '-',
          [f('agent', x.optString('agent') ?? '-')],
          [
            if (x.optString('why') != null) x.optString('why')!,
            ...?x.optStrings('reasons'),
          ],
        ),
      ),
      data: lines(
        'data_health',
        (d) => ReportLine(
          '${d.optString('symbol') ?? '-'} ${d.optString('venue') ?? ''}'
              .trim(),
          [
            f('status', d.optString('status') ?? '-'),
            f('last_bar', d.optString('last_bar') ?? '-'),
            f('sessions_behind', _raw(d['sessions_behind'])),
            f('source', d.optString('source') ?? '-'),
          ],
          d.optStrings('reasons') ?? const [],
        ),
      ),
      portfolios: lines(
        'portfolios',
        (p) => ReportLine(
            p.optString('strategy') ?? '-',
            [
              for (final k in ['cash', 'realised', 'unrealised', 'fees'])
                f(k, _amounts(p.optObjects(k, (a) => a))),
            ],
            p.optStrings('unpriced') ?? const []),
      ),
      performance: lines(
        'performance',
        (p) => ReportLine(
            p.optString('strategy') ?? '-',
            [
              f('since', p.optString('since') ?? '-'),
              f('return', _pct(p.optNumber('return'))),
              f('benchmark_return', _pct(p.optNumber('benchmark_return'))),
              f('max_drawdown', _pct(p.optNumber('max_drawdown'))),
            ],
            p.optStrings('flags') ?? const []),
      ),
      problems: lines(
        'problems',
        (p) => ReportLine(
            p.optString('agent') ?? '-',
            [
              f('action', p.optString('action') ?? '-'),
              f('outcome', p.optString('outcome') ?? '-'),
            ],
            p.optStrings('reasons') ?? const []),
      ),
      markdown: r.optString('markdown') ?? '',
      commentaryStatus: r.optObject('commentary')?.optString('status'),
      commentaryReason: r.optObject('commentary')?.optString('reason'),
      commentaryText: r.optObject('commentary')?.optString('text'),
      commentaryModel: r.optObject('commentary')?.optString('model'),
    );
  }
}

abstract interface class FinanceIntelRepository {
  Future<IntelOverview> overview();
  Future<IntelPage<IntelStrategy>> strategies({
    String? state,
    required int offset,
    required int limit,
  });
  Future<IntelPage<IntelRun>> runs(
    String strategyId, {
    required int offset,
    required int limit,
  });
  Future<IntelPage<IntelDecision>> decisions({
    String? agent,
    required int offset,
    required int limit,
  });
  Future<EquityCurve> equity(String portfolioId, {required int days});

  /// The person's kill switch: true stops every paper and shadow executor.
  Future<void> setKillSwitch(bool engaged);

  // Actions (finance/intel_control_api.py, finance:manage). SIMULATED: none
  // of them approves an order or trades live.
  Future<List<StrategyRule>> rules();
  Future<List<IntelInstrument>> instruments();
  Future<IntelStrategy> createStrategy({
    required String key,
    required List<String> instrumentIds,
    required String reason,
  });
  Future<IntelStrategy> moveStrategy(
    String strategyId, {
    required String state,
    required String reason,
  });
  Future<IntelCycle> runSupervisor(String phase);
  Future<int> pauseAll(String reason);
  Future<IntelInstrument> createInstrument(
      {required String symbol,
      required String assetClass,
      required String venue,
      required String currency,
      String? listedOn});
  Future<BarsImport> importBars(String instrumentId,
      {required String payload, required String source});

  /// A SIMULATED `backtest` or `walk_forward` run; returns its verdict.
  Future<String> evaluate(String strategyId, {required String kind});
  Future<IntelStrategy> choosePortfolio(String strategyId, String portfolioId);

  // Post-close daily reports, read only.
  Future<IntelPage<DailyReportItem>> dailyReports({
    required int offset,
    required int limit,
  });
  Future<DailyReport> dailyReport(String reportId);
}

class ApiFinanceIntelRepository implements FinanceIntelRepository {
  ApiFinanceIntelRepository(this.api);
  final AnumApiClient api;
  static const base = '/api/v1/finance';

  String _q(Map<String, Object?> values) {
    final kept = {
      for (final e in values.entries)
        if (e.value != null && '${e.value}'.isNotEmpty) e.key: '${e.value}',
    };
    return kept.isEmpty ? '' : '?${Uri(queryParameters: kept).query}';
  }

  Future<JsonReader> _get(String path) async =>
      JsonReader.of(await api.request('GET', '$base$path'));

  @override
  Future<IntelOverview> overview() async =>
      IntelOverview.fromJson(await _get('/intel/overview'));

  @override
  Future<IntelPage<IntelStrategy>> strategies({
    String? state,
    required int offset,
    required int limit,
  }) async =>
      IntelPage.read(
        await _get(
          '/intel/strategies${_q({
                'state': state,
                'limit': limit,
                'offset': offset
              })}',
        ),
        IntelStrategy.fromJson,
      );

  @override
  Future<IntelPage<IntelRun>> runs(
    String strategyId, {
    required int offset,
    required int limit,
  }) async =>
      IntelPage.read(
        await _get(
          '/intel/strategies/${Uri.encodeComponent(strategyId)}/runs${_q({
                'limit': limit,
                'offset': offset
              })}',
        ),
        IntelRun.fromJson,
      );

  @override
  Future<IntelPage<IntelDecision>> decisions({
    String? agent,
    required int offset,
    required int limit,
  }) async =>
      IntelPage.read(
        await _get(
          '/intel/decisions${_q({
                'agent': agent,
                'limit': limit,
                'offset': offset
              })}',
        ),
        IntelDecision.fromJson,
      );

  @override
  Future<EquityCurve> equity(
    String portfolioId, {
    required int days,
  }) async =>
      EquityCurve.fromJson(
        await _get(
          '/intel/portfolios/${Uri.encodeComponent(portfolioId)}/equity${_q({
                'days': days
              })}',
        ),
      );

  @override
  Future<void> setKillSwitch(bool engaged) async {
    await api.request(
      'PUT',
      '$base/execution/kill-switch',
      body: {
        'scope': 'person',
        'engaged': engaged,
        if (engaged)
          'reason': 'Stopped from the trading dashboard on the phone.',
      },
    );
  }

  @override
  Future<List<StrategyRule>> rules() async =>
      JsonReader.of(await api.request('GET', '$base/intel/rules'))
          .optObjects('data', StrategyRule.fromJson);

  @override
  Future<List<IntelInstrument>> instruments() async =>
      JsonReader.of(await api.request('GET', '$base/intel/instruments'))
          .optObjects('data', IntelInstrument.fromJson);

  @override
  Future<IntelStrategy> createStrategy({
    required String key,
    required List<String> instrumentIds,
    required String reason,
  }) async =>
      IntelStrategy.fromJson(
        JsonReader.of(
          await api.request(
            'POST',
            '$base/intel/strategies',
            body: {
              'key': key,
              'instrument_ids': instrumentIds,
              'reason': reason
            },
          ),
        ),
      );

  @override
  Future<IntelStrategy> moveStrategy(
    String strategyId, {
    required String state,
    required String reason,
  }) async =>
      IntelStrategy.fromJson(
        JsonReader.of(
          await api.request(
            'POST',
            '$base/intel/strategies/${Uri.encodeComponent(strategyId)}/transition',
            body: {'state': state, 'reason': reason},
          ),
        ),
      );

  @override
  Future<IntelCycle> runSupervisor(String phase) async => IntelCycle.fromJson(
        JsonReader.of(
          await api.request(
            'POST',
            '$base/intel/supervisor/run',
            body: {'phase': phase},
          ),
        ),
      );

  @override
  Future<int> pauseAll(String reason) async {
    final r = JsonReader.of(
      await api.request(
        'POST',
        '$base/intel/pause-all',
        body: {'reason': reason},
      ),
    );
    return r.optStrings('paused')?.length ?? 0;
  }

  @override
  Future<IntelPage<DailyReportItem>> dailyReports({
    required int offset,
    required int limit,
  }) async =>
      IntelPage.read(
        await _get(
            '/intel/daily-reports${_q({'limit': limit, 'offset': offset})}'),
        DailyReportItem.fromJson,
      );

  @override
  Future<DailyReport> dailyReport(String reportId) async =>
      DailyReport.fromJson(
        await _get('/intel/daily-reports/${Uri.encodeComponent(reportId)}'),
      );

  @override
  Future<IntelInstrument> createInstrument(
          {required String symbol,
          required String assetClass,
          required String venue,
          required String currency,
          String? listedOn}) async =>
      IntelInstrument.fromJson(JsonReader.of(
          await api.request('POST', '$base/intel/instruments', body: {
        'symbol': symbol,
        'asset_class': assetClass,
        'venue': venue,
        'currency': currency,
        if (listedOn != null) 'listed_on': listedOn,
      })));

  @override
  Future<BarsImport> importBars(String instrumentId,
          {required String payload, required String source}) async =>
      BarsImport.fromJson(JsonReader.of(await api.request('POST',
          '$base/intel/instruments/${Uri.encodeComponent(instrumentId)}/bars',
          body: {
            'provider': 'csv_import',
            'payload': payload,
            'source': source
          })));

  @override
  Future<String> evaluate(String strategyId, {required String kind}) async =>
      JsonReader.of(await api.request('POST',
              '$base/intel/strategies/${Uri.encodeComponent(strategyId)}/evaluate',
              body: {'kind': kind}))
          .optString('verdict') ??
      '';

  @override
  Future<IntelStrategy> choosePortfolio(
          String strategyId, String portfolioId) async =>
      IntelStrategy.fromJson(JsonReader.of(await api.request('POST',
          '$base/intel/strategies/${Uri.encodeComponent(strategyId)}/portfolio',
          body: {'portfolio_id': portfolioId})));
}
