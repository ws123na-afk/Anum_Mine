import '../../data/json_read.dart';
import 'finance_models.dart';

/// Finance stages 3 to 5 on the phone (docs/finance.md): safe to spend, the
/// Guardian rules and read-only connections. Money stays a decimal string
/// next to its currency; nothing here moves money.

/// One line of the safe-to-spend figure: positive for money you have,
/// negative for a deduction, with why it is there ([note], [params]).
class SafeLine {
  const SafeLine(
      {required this.kind,
      required this.label,
      required this.currency,
      required this.amount,
      required this.note,
      this.params = const {}});
  final String kind, label, currency, amount, note;
  final Map<String, String> params;

  factory SafeLine.fromJson(JsonReader r) => SafeLine(
        kind: r.string('kind'),
        label: r.string('label'),
        currency: r.string('currency'),
        amount: r.string('amount'),
        note: r.string('note'),
        params: {
          for (final e in (r.optMap('params') ?? const {}).entries)
            e.key: '${e.value}'
        },
      );
}

class CurrencySafe {
  const CurrencySafe(
      {required this.currency,
      required this.safeToSpend,
      required this.perDay,
      required this.short,
      required this.lines,
      this.nextPayOn,
      this.expectedPay,
      this.daysToPayday});
  final String currency, safeToSpend, perDay;
  final bool short;
  final List<SafeLine> lines;

  /// With a salary profile in this currency: the next usual pay day, the net
  /// expected then and the days until it (older servers leave them out).
  final String? nextPayOn, expectedPay;
  final int? daysToPayday;

  /// Minimum payments whose due date passed with no payment matched yet.
  List<SafeLine> get overdueMinimums =>
      lines.where((l) => l.note == 'minimum_overdue').toList();

  factory CurrencySafe.fromJson(JsonReader r) => CurrencySafe(
        currency: r.string('currency'),
        safeToSpend: r.string('safe_to_spend'),
        perDay: r.string('per_day'),
        short: r.optBool('short') ?? false,
        lines: r.optObjects('lines', SafeLine.fromJson),
        nextPayOn: r.optString('next_pay_on'),
        expectedPay: switch (r.json['expected_pay']) {
          final String text => text,
          final num number => number.toString(),
          _ => null,
        },
        daysToPayday: r.optInt('days_to_payday'),
      );
}

class SafeToSpend {
  const SafeToSpend(
      {required this.monthEnd,
      required this.daysLeft,
      required this.currencies,
      required this.excluded,
      this.combined,
      this.forecasts = const [],
      this.reserves = const [],
      this.forecastFailed = false});
  final String monthEnd;
  final int daysLeft;
  final List<CurrencySafe> currencies;
  final List<SafeLine> excluded;
  final CurrencyAmount? combined;

  /// The API's forecast per currency (GET /finance/forecast, docs/finance.md#forecast)
  /// and the emergency reserves (GET /finance/reserve); read only.
  final List<CurrencyForecast> forecasts;
  final List<ReserveProgress> reserves;

  /// The forecast could not be read: the screen says so instead of a figure.
  final bool forecastFailed;

  SafeToSpend withForecast(
          List<CurrencyForecast> forecasts, List<ReserveProgress> reserves,
          {bool failed = false}) =>
      SafeToSpend(
          monthEnd: monthEnd,
          daysLeft: daysLeft,
          currencies: currencies,
          excluded: excluded,
          combined: combined,
          forecasts: forecasts,
          reserves: reserves,
          forecastFailed: failed);

  factory SafeToSpend.fromJson(JsonReader r) {
    final combined = r.optObject('combined');
    return SafeToSpend(
      monthEnd: r.string('month_end'),
      daysLeft: r.optInt('days_left') ?? 1,
      currencies: r.optObjects('currencies', CurrencySafe.fromJson),
      excluded: r.optObjects('excluded', SafeLine.fromJson),
      combined: combined == null
          ? null
          : CurrencyAmount(
              combined.string('currency'), combined.string('safe_to_spend')),
    );
  }
}

/// The expected balance on one day with its likely range (an estimate).
class ForecastDay {
  const ForecastDay(
      {required this.on,
      required this.expected,
      required this.low,
      required this.high});
  final String on, expected, low, high;

  factory ForecastDay.fromJson(JsonReader r) => ForecastDay(
      on: r.string('on'),
      expected: r.string('expected'),
      low: r.string('low'),
      high: r.string('high'));
}

/// The first day the expected balance goes below zero or the reserve.
class ForecastWarning {
  const ForecastWarning(
      {required this.kind, required this.on, required this.threshold});
  final String kind, on, threshold;

  factory ForecastWarning.fromJson(JsonReader r) => ForecastWarning(
      kind: r.string('kind'),
      on: r.string('on'),
      threshold: r.string('threshold'));
}

/// One currency of the forecast: today's actual cash and the month end.
class CurrencyForecast {
  const CurrencyForecast(
      {required this.currency,
      required this.actual,
      required this.monthEnd,
      this.warnings = const []});
  final String currency, actual;
  final ForecastDay monthEnd;
  final List<ForecastWarning> warnings;

  /// The warning to show first: below zero, then below the reserve, then
  /// "may go below zero", within this month.
  ForecastWarning? get firstWarning {
    for (final kind in const [
      'below_zero',
      'below_reserve',
      'may_go_below_zero'
    ]) {
      for (final w in warnings) {
        if (w.kind == kind && w.on.compareTo(monthEnd.on) <= 0) return w;
      }
    }
    return null;
  }

  factory CurrencyForecast.fromJson(JsonReader r) => CurrencyForecast(
        currency: r.string('currency'),
        actual: r.string('actual'),
        monthEnd: ForecastDay.fromJson(r.object('month_end')),
        warnings: r.optObjects('warnings', ForecastWarning.fromJson),
      );
}

/// An emergency reserve's progress (docs/finance.md#emergency-reserve).
class ReserveProgress {
  const ReserveProgress(
      {required this.currency,
      required this.name,
      required this.saved,
      required this.target,
      this.progressPercent,
      this.monthsCovered});
  final String currency, name, saved, target;
  final int? progressPercent;
  final String? monthsCovered;

  /// 0 to 1 for a progress bar.
  double get fraction => ((progressPercent ?? 0).clamp(0, 100)) / 100;

  factory ReserveProgress.fromJson(JsonReader r) => ReserveProgress(
        currency: r.string('currency'),
        name: r.string('name'),
        saved: r.string('saved'),
        target: r.string('target'),
        progressPercent: r.optInt('progress_percent'),
        monthsCovered: r.optString('months_covered'),
      );
}

/// The person's rules as plain sentence parts: (key, values) pairs the
/// screen turns into text in the person's language.
typedef RuleLine = (String key, Map<String, Object> values);

List<RuleLine> describeRules(Map<String, Object?> rules,
    {String Function(String category)? category}) {
  final name = category ?? (String c) => c;
  List<Map<String, Object?>> list(String key) => [
        for (final item in (rules[key] as List<Object?>? ?? const []))
          if (item is Map) item.cast<String, Object?>()
      ];
  String money(Map<String, Object?> l) =>
      formatAmount('${l['amount']}', '${l['currency']}');
  final lines = <RuleLine>[];
  final currencies = (rules['allowed_currencies'] as List<Object?>? ?? []);
  if (currencies.isNotEmpty) {
    lines.add(('rule.currencies', {'list': currencies.join(', ')}));
  }
  for (final l in list('max_single')) {
    lines.add(('rule.single', {'amount': money(l)}));
  }
  for (final l in list('max_daily_total')) {
    lines.add(('rule.daily', {'amount': money(l)}));
  }
  for (final c in list('category_caps')) {
    lines.add((
      'rule.cap',
      {'category': name('${c['category']}'), 'amount': money(c)}
    ));
  }
  final blocked = (rules['blocked_categories'] as List<Object?>? ?? []);
  if (blocked.isNotEmpty) {
    lines.add((
      'rule.blockedCategories',
      {'list': blocked.map((c) => name('$c')).join(', ')}
    ));
  }
  final merchants = (rules['blocked_merchants'] as List<Object?>? ?? []);
  if (merchants.isNotEmpty) {
    lines.add(('rule.blockedMerchants', {'list': merchants.join(', ')}));
  }
  for (final l in list('approval_above')) {
    lines.add(('rule.approval', {'amount': money(l)}));
  }
  final quiet = rules['quiet_hours'];
  if (quiet is Map) {
    lines.add((
      'rule.quiet',
      {'start': '${quiet['start']}', 'end': '${quiet['end']}'}
    ));
  }
  final cooling = rules['cooling_off'];
  if (cooling is Map) {
    lines.add(('rule.cooling', {'n': '${cooling['hours']}'}));
  }
  return lines;
}

class GuardianChange {
  const GuardianChange(
      {required this.id,
      required this.proposedBy,
      required this.payloadHash,
      required this.rules,
      this.reason,
      this.loosens = const []});
  final String id, proposedBy, payloadHash;
  final String? reason;
  final Map<String, Object?> rules;
  final List<String> loosens;

  factory GuardianChange.fromJson(JsonReader r) => GuardianChange(
        id: r.string('id'),
        proposedBy: r.string('proposed_by'),
        payloadHash: r.string('payload_hash'),
        reason: r.optString('reason'),
        rules: r.optMap('rules') ?? const {},
        loosens: r.optStrings('loosens') ?? const [],
      );
}

class GuardianAlert {
  const GuardianAlert(this.code, this.severity, this.params);
  final String code, severity;
  final Map<String, String> params;
  factory GuardianAlert.fromJson(JsonReader r) => GuardianAlert(
        r.string('code'),
        r.string('severity'),
        {
          for (final e in (r.optMap('params') ?? const {}).entries)
            e.key: '${e.value}'
        },
      );
}

class GuardianView {
  const GuardianView(
      {required this.version, required this.rules, required this.pending});
  final int version;
  final Map<String, Object?> rules;
  final List<GuardianChange> pending;

  factory GuardianView.fromJson(JsonReader r) => GuardianView(
        version: r.optInt('version') ?? 0,
        rules: r.optMap('rules') ?? const {},
        pending: r.optObjects('pending', GuardianChange.fromJson),
      );
}

class FinanceConnection {
  const FinanceConnection(
      {required this.id,
      required this.label,
      required this.provider,
      required this.status,
      this.lastSuccessAt,
      this.lastError});
  final String id, label, provider, status;
  final String? lastSuccessAt, lastError;

  factory FinanceConnection.fromJson(JsonReader r) => FinanceConnection(
        id: r.string('id'),
        label: r.string('label'),
        provider: r.string('provider'),
        status: r.string('status'),
        lastSuccessAt: r.optString('last_success_at'),
        lastError: r.optString('last_error'),
      );
}
