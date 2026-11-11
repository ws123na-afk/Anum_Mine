import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/readable_error.dart';
import '../../src/localization/anum_localizations.dart';
import 'admin_repository.dart';

/// The spend breakdown on Model budgets (docs/model-gateway.md#spend-breakdown):
/// `GET /api/v1/model-budgets/usage` grouped by day, month, person, agent or
/// feature, the same rows as the web's "Where the spend goes", paged, and the
/// whole range as CSV through the system save dialog. Owners only: the API
/// answers 403 to everyone else and the card says who can see it.

enum UsageGroup { day, month, user, agent, feature }

enum UsageScope { workspace, tenant }

enum UsageMeasure { cost, tokens }

enum UsagePreset { month, last30, last3m, last12m, custom }

/// Groups per page; the API caps a page at 500.
const usagePageSize = 50;

/// The API refuses a longer range.
const maxUsageRangeDays = 400;

class UsageRange {
  const UsageRange(this.from, this.to);
  final DateTime from, to;

  /// The API's `YYYY-MM-DD` for a UTC day.
  static String day(DateTime value) =>
      '${value.year.toString().padLeft(4, '0')}-${value.month.toString().padLeft(2, '0')}-${value.day.toString().padLeft(2, '0')}';

  String get fromDay => day(from);
  String get toDay => day(to);

  /// `to` not before `from`, at most 400 days apart (both inclusive).
  bool get valid =>
      !to.isBefore(from) && to.difference(from).inDays <= maxUsageRangeDays;

  @override
  bool operator ==(Object other) =>
      other is UsageRange && other.fromDay == fromDay && other.toDay == toDay;

  @override
  int get hashCode => Object.hash(fromDay, toDay);
}

DateTime _utc(int y, int m, int d) => DateTime.utc(y, m, d);

/// The `from` and `to` a preset means on [today] (UTC days), as on the web.
UsageRange rangeFor(UsagePreset preset, DateTime today, {UsageRange? custom}) {
  final t = today.toUtc();
  final y = t.year, m = t.month, d = t.day;
  return switch (preset) {
    UsagePreset.month => UsageRange(_utc(y, m, 1), _utc(y, m, d)),
    UsagePreset.last30 => UsageRange(_utc(y, m, d - 29), _utc(y, m, d)),
    UsagePreset.last3m => UsageRange(_utc(y, m - 2, 1), _utc(y, m, d)),
    UsagePreset.last12m => UsageRange(_utc(y, m - 11, 1), _utc(y, m, d)),
    UsagePreset.custom => custom ?? UsageRange(_utc(y, m, 1), _utc(y, m, d)),
  };
}

class UsageQuery {
  const UsageQuery(
      {required this.group, required this.scope, required this.range});
  final UsageGroup group;
  final UsageScope scope;
  final UsageRange range;

  /// The request path for one page, or the whole range as CSV.
  String path({int offset = 0, int limit = usagePageSize, bool csv = false}) {
    final query = <String, String>{
      'group_by': group.name,
      'scope': scope.name,
      'from': range.fromDay,
      'to': range.toDay,
      if (csv)
        'format': 'csv'
      else ...{
        'limit': '$limit',
        'offset': '$offset',
      },
    };
    return Uri(path: '/api/v1/model-budgets/usage', queryParameters: query)
        .toString();
  }

  /// The file name the API sends with the CSV, used when it sends none.
  String get csvFileName =>
      'model-usage-${scope.name}-${group.name}-${range.fromDay}-${range.toDay}.csv';
}

class UsageRow {
  const UsageRow({
    required this.key,
    required this.label,
    required this.calls,
    required this.inputTokens,
    required this.outputTokens,
    required this.costUsd,
    required this.unpricedCalls,
  });
  factory UsageRow.fromJson(JsonReader j) => UsageRow(
        key: j.optString('key') ?? '',
        label: j.optString('label'),
        calls: j.optInt('calls') ?? 0,
        inputTokens: j.optInt('input_tokens') ?? 0,
        outputTokens: j.optInt('output_tokens') ?? 0,
        costUsd: j.optNumber('estimated_cost_usd') ?? 0,
        unpricedCalls: j.optInt('unpriced_calls') ?? 0,
      );
  final String key;
  final String? label;
  final int calls, inputTokens, outputTokens, unpricedCalls;
  final double costUsd;

  int get tokens => inputTokens + outputTokens;
  double measure(UsageMeasure m) =>
      m == UsageMeasure.cost ? costUsd : tokens.toDouble();
}

class UsageBreakdown {
  const UsageBreakdown({
    required this.rows,
    required this.total,
    required this.count,
    required this.fromDay,
    required this.toDay,
    this.recordedSince,
  });
  factory UsageBreakdown.fromJson(JsonReader j) => UsageBreakdown(
        rows: j.optObjects('rows', UsageRow.fromJson),
        total: UsageRow.fromJson(j.optObject('total') ?? JsonReader(const {})),
        count: j.optInt('count') ?? 0,
        fromDay: j.optString('from_day') ?? '',
        toDay: j.optString('to_day') ?? '',
        recordedSince: j.optString('recorded_since'),
      );
  final List<UsageRow> rows;

  /// The whole period's sums (every group, not only this page).
  final UsageRow total;

  /// How many groups the period holds in all.
  final int count;
  final String fromDay, toDay;

  /// The first recorded day when the range starts before it, else null.
  final String? recordedSince;
}

/// Each row's share of the period's total, whole percents.
int shareOf(UsageRow row, UsageRow total, UsageMeasure measure) {
  final whole = total.measure(measure);
  return whole > 0 ? (row.measure(measure) / whole * 100).round() : 0;
}

/// Bar lengths as fractions of the largest loaded row (at least 0.02 so a
/// tiny value still shows).
List<double> barFractions(List<UsageRow> rows, UsageMeasure measure) {
  final top = rows.fold<double>(0, (max, r) {
    final v = r.measure(measure);
    return v > max ? v : max;
  });
  return [
    for (final r in rows)
      top > 0 ? (r.measure(measure) / top).clamp(0.02, 1.0).toDouble() : 0
  ];
}

const _features = {
  'tasks', 'voice', 'assistant', 'email', 'knowledge', 'meetings', //
  'business', 'marketing', 'finance', 'calls', 'speech', 'browser',
  'attendance', 'executive', 'whatsapp', 'commerce', 'developer', 'other',
};

const _monthsEn = [
  'January', 'February', 'March', 'April', 'May', 'June', 'July', //
  'August', 'September', 'October', 'November', 'December',
];
const _monthsAr = [
  'يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو', 'يوليو', //
  'أغسطس', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر',
];

/// A UTC day or month as words ("3 November 2026", "November 2026").
String usageDate(String key, AnumLocalizations l, {bool month = false}) {
  final date = DateTime.tryParse(key);
  if (date == null) return key.isEmpty ? '-' : key;
  final name = (l.isArabic ? _monthsAr : _monthsEn)[date.month - 1];
  return month ? '$name ${date.year}' : '${date.day} $name ${date.year}';
}

/// The words a row's key reads as: a date, a month, a person, an agent's
/// name or a feature's name.
String usageRowLabel(UsageGroup group, UsageRow row, AnumLocalizations l,
    {String? currentUserId}) {
  switch (group) {
    case UsageGroup.day:
      return usageDate(row.key, l);
    case UsageGroup.month:
      return usageDate(row.key, l, month: true);
    case UsageGroup.user:
      if (row.key.isEmpty) return '-';
      return row.key == currentUserId
          ? l.f('ad.use.user.you', {'user': row.key})
          : row.key;
    case UsageGroup.agent:
      return row.label ?? (row.key.isEmpty ? '-' : row.key);
    case UsageGroup.feature:
      return _features.contains(row.key)
          ? l.t('ad.use.feature.${row.key}')
          : (row.key.isEmpty ? '-' : row.key);
  }
}

enum UsagePhase { loading, ready, denied, error }

/// The breakdown card's state: the choices, the loaded pages, Load more and
/// the CSV export.
class BudgetUsageController extends ChangeNotifier {
  BudgetUsageController(this.repository, {DateTime Function()? now})
      : _now = now ?? DateTime.now;
  final AdminRepository repository;
  final DateTime Function() _now;

  UsageGroup group = UsageGroup.day;
  UsageScope scope = UsageScope.workspace;
  UsageMeasure measure = UsageMeasure.cost;
  UsagePreset preset = UsagePreset.month;
  UsageRange? custom;

  UsagePhase phase = UsagePhase.loading;
  String? error;
  UsageBreakdown? breakdown;
  List<UsageRow> rows = const [];
  bool loadingMore = false;
  String? moreError;

  /// The CSV export: running, and its last result (null when none).
  bool exporting = false;
  String? exportNotice;
  bool exportFailed = false;

  int _request = 0;
  bool _disposed = false;

  UsageRange get range => rangeFor(preset, _now(), custom: custom);
  UsageQuery get query => UsageQuery(group: group, scope: scope, range: range);
  bool get hasMore => breakdown != null && rows.length < breakdown!.count;

  Future<void> load() async {
    final id = ++_request;
    phase = UsagePhase.loading;
    error = moreError = null;
    _notify();
    try {
      final page = await repository.budgetUsage(query);
      if (id != _request) return;
      breakdown = page;
      rows = page.rows;
      phase = UsagePhase.ready;
    } on ApiException catch (e) {
      if (id != _request) return;
      phase = e.isPermissionDenied ? UsagePhase.denied : UsagePhase.error;
      error = e.readable;
    } on Object catch (e) {
      if (id != _request) return;
      phase = UsagePhase.error;
      error = readableError(e);
    }
    _notify();
  }

  Future<void> more() async {
    if (loadingMore || !hasMore) return;
    final id = _request;
    loadingMore = true;
    moreError = null;
    _notify();
    try {
      final page = await repository.budgetUsage(query, offset: rows.length);
      if (id != _request) return;
      rows = [...rows, ...page.rows];
      breakdown = page;
    } on Object catch (e) {
      if (id != _request) return;
      moreError = readableError(e);
    } finally {
      loadingMore = false;
      _notify();
    }
  }

  Future<void> setGroup(UsageGroup value) => _change(() => group = value);
  Future<void> setScope(UsageScope value) => _change(() => scope = value);
  Future<void> setPreset(UsagePreset value) => _change(() => preset = value);

  /// A custom period; refused (false) when it is not one the API accepts.
  Future<bool> setCustom(UsageRange value) async {
    if (!value.valid) return false;
    await _change(() {
      custom = value;
      preset = UsagePreset.custom;
    });
    return true;
  }

  void setMeasure(UsageMeasure value) {
    measure = value;
    _notify();
  }

  Future<void> _change(void Function() apply) {
    apply();
    return load();
  }

  /// Saves the whole range as CSV through the system save dialog.
  Future<void> export(AnumLocalizations l) async {
    if (exporting) return;
    exporting = true;
    exportNotice = null;
    exportFailed = false;
    _notify();
    try {
      final saved =
          await repository.exportBudgetUsage(query, l.t('ad.use.export'));
      exportNotice = l.t(saved ? 'ad.use.exported' : 'ad.use.exportCancelled');
    } on Object catch (e) {
      exportFailed = true;
      exportNotice = l.f('ad.use.exportFailed', {'message': readableError(e)});
    } finally {
      exporting = false;
      _notify();
    }
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
