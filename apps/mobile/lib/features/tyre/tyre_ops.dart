import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';

/// Tyre planning beyond the dashboard (docs/tyre-planning.md): sync now, the
/// purchase plan, generated reports with delivery, and report schedules.
/// Everyone with `tyre:read` reads; `tyre:operate` (owners and members)
/// syncs, generates, deletes, delivers and schedules. The server checks both.

const reportKinds = [
  'fleet_health',
  'wear_rates',
  'pressure_compliance',
  'cost_per_distance',
  'scrap_reasons',
  'brand_performance',
  'removal_forecast',
  'purchase_plan',
];

enum OpsLoad { idle, loading, ready, denied, error }

class PlanRow {
  const PlanRow({
    required this.month,
    required this.size,
    required this.brand,
    required this.due,
    required this.safety,
    required this.fromStock,
    required this.toBuy,
    this.unitPrice,
    this.estimatedCost,
  });
  final String month, size, brand;
  final int due, safety, fromStock, toBuy;
  final double? unitPrice, estimatedCost;

  factory PlanRow.fromJson(JsonReader r) => PlanRow(
        month: r.string('month'),
        size: r.string('size'),
        brand: r.optString('brand') ?? '',
        due: r.integer('due'),
        safety: r.optInt('safety') ?? 0,
        fromStock: r.optInt('from_stock') ?? 0,
        toBuy: r.integer('to_buy'),
        unitPrice: r.optNumber('unit_price'),
        estimatedCost: r.optNumber('estimated_cost'),
      );
}

class PurchasePlan {
  const PurchasePlan({
    required this.currency,
    required this.rows,
    required this.totalTyres,
    required this.unpricedRows,
    this.totalCost,
  });
  final String currency;
  final List<PlanRow> rows;
  final int totalTyres, unpricedRows;
  final double? totalCost;

  factory PurchasePlan.fromJson(JsonReader r) => PurchasePlan(
        currency: r.optString('currency') ?? '',
        rows: r.objects('rows', PlanRow.fromJson),
        totalTyres: r.integer('total_tyres'),
        unpricedRows: r.optInt('unpriced_rows') ?? 0,
        totalCost: r.optNumber('total_cost'),
      );
}

class ReportHighlight {
  const ReportHighlight(this.label, this.value);
  final String label;
  final String value;
}

class ReportTable {
  const ReportTable(this.title, this.columns, this.rows);
  final String title;
  final List<String> columns;
  final List<List<String>> rows;
}

class TyreReportItem {
  const TyreReportItem({
    required this.id,
    required this.kind,
    required this.title,
    required this.createdAt,
    required this.source,
    required this.highlights,
    required this.deliveries,
    this.periodFrom,
    this.periodTo,
    this.tables = const [],
    this.notes = const [],
  });
  final String id, kind, title, source;
  final String? periodFrom, periodTo;
  final DateTime createdAt;
  final List<ReportHighlight> highlights;
  final List<Map<String, Object?>> deliveries;
  final List<ReportTable> tables;
  final List<String> notes;

  static String _cell(Object? v) => v == null
      ? '-'
      : v is double
          ? (v == v.roundToDouble() ? v.toStringAsFixed(0) : '$v')
          : '$v';

  static List<ReportHighlight> _highlights(List<Object?> raw) => [
        for (final item in raw)
          if (item is Map)
            ReportHighlight('${item['label']}', _cell(item['value'])),
      ];

  factory TyreReportItem.fromJson(JsonReader r) {
    // A list row has `highlights` at the top; the full report has them inside
    // `content` together with its tables and notes.
    final content = r.optMap('content');
    final rawHighlights =
        (content?['highlights'] ?? r.json['highlights']) as List<Object?>? ??
            const [];
    final tables = <ReportTable>[];
    for (final t in (content?['tables'] as List<Object?>? ?? const [])) {
      if (t is! Map) continue;
      tables.add(ReportTable(
        '${t['title'] ?? ''}',
        [for (final c in (t['columns'] as List<Object?>? ?? const [])) '$c'],
        [
          for (final row in (t['rows'] as List<Object?>? ?? const []))
            if (row is List) [for (final cell in row) _cell(cell)],
        ],
      ));
    }
    return TyreReportItem(
      id: r.string('id'),
      kind: r.string('kind'),
      title: r.string('title'),
      periodFrom: r.optString('period_from'),
      periodTo: r.optString('period_to'),
      createdAt: r.date('created_at'),
      source: r.optString('source') ?? 'manual',
      highlights: _highlights(rawHighlights),
      deliveries: [
        for (final d in (r.optList('deliveries') ?? const []))
          if (d is Map<String, Object?>) d,
      ],
      tables: tables,
      notes: [
        for (final n in (content?['notes'] as List<Object?>? ?? const [])) '$n'
      ],
    );
  }
}

class ReportSchedule {
  const ReportSchedule({
    required this.id,
    required this.kind,
    required this.title,
    required this.cadence,
    required this.description,
    required this.enabled,
    this.nextRunAt,
  });
  final String id, kind, title, cadence, description;
  final bool enabled;
  final DateTime? nextRunAt;

  factory ReportSchedule.fromJson(JsonReader r) => ReportSchedule(
        id: r.string('id'),
        kind: r.string('kind'),
        title: r.string('title'),
        cadence: r.optString('cadence') ?? '',
        description: r.optString('description') ?? '',
        enabled: r.optBool('enabled') ?? true,
        nextRunAt: r.optDate('next_run_at'),
      );
}

class DeliveryResult {
  const DeliveryResult(this.status, this.message);
  final String status, message;
}

class OpsPage<T> {
  const OpsPage(this.items, this.total);
  final List<T> items;
  final int total;
}

abstract interface class TyreOpsRepository {
  Future<int> sync();
  Future<PurchasePlan> plan(int months);
  Future<OpsPage<TyreReportItem>> reports(
      {String? kind, required int offset, int limit});
  Future<TyreReportItem> report(String id);
  Future<TyreReportItem> generate(
      String kind, String? periodFrom, String? periodTo);
  Future<void> deleteReport(String id);
  Future<DeliveryResult> deliver(
      String id, String channel, String to, String format);
  Future<List<String>> deliveryChannels();
  Future<List<ReportSchedule>> schedules();
  Future<ReportSchedule> createSchedule(Map<String, Object?> fields);
  Future<void> deleteSchedule(String id);
}

class ApiTyreOpsRepository implements TyreOpsRepository {
  const ApiTyreOpsRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/tyre';

  String _id(String v) => Uri.encodeComponent(v);

  Future<JsonReader> _call(String method, String path,
          {Map<String, Object?>? body}) async =>
      JsonReader.of(await api.request(method, '$_base$path', body: body));

  Future<List<Object?>> _list(String path) async {
    // A bare list is wrapped as `data` by the transport.
    final raw = await api.request('GET', '$_base$path');
    final items = raw['data'];
    return items is List ? items : const [];
  }

  @override
  Future<int> sync() async =>
      (await _call('POST', '/sync')).optInt('new_alerts') ?? 0;

  @override
  Future<PurchasePlan> plan(int months) async => PurchasePlan.fromJson(
      await _call('GET', '/purchase-plan?months=$months'));

  @override
  Future<OpsPage<TyreReportItem>> reports(
      {String? kind, required int offset, int limit = 20}) async {
    final q = Uri(queryParameters: {
      if (kind != null) 'kind': kind,
      'offset': '$offset',
      'limit': '$limit',
    }).query;
    final r = await _call('GET', '/reports?$q');
    return OpsPage(
        r.objects('items', TyreReportItem.fromJson), r.integer('total'));
  }

  @override
  Future<TyreReportItem> report(String id) async =>
      TyreReportItem.fromJson(await _call('GET', '/reports/${_id(id)}'));

  @override
  Future<TyreReportItem> generate(
          String kind, String? periodFrom, String? periodTo) async =>
      TyreReportItem.fromJson(await _call('POST', '/reports', body: {
        'kind': kind,
        if (periodFrom != null) 'period_from': periodFrom,
        if (periodTo != null) 'period_to': periodTo,
      }));

  @override
  Future<void> deleteReport(String id) =>
      api.request('DELETE', '$_base/reports/${_id(id)}');

  @override
  Future<DeliveryResult> deliver(
      String id, String channel, String to, String format) async {
    final r = await _call('POST', '/reports/${_id(id)}/deliver',
        body: {'channel': channel, 'to': to, 'format': format});
    return DeliveryResult(r.string('status'), r.optString('message') ?? '');
  }

  @override
  Future<List<String>> deliveryChannels() async {
    final r = await _call('GET', '/delivery-channels');
    return r.optStrings('channels') ?? const [];
  }

  @override
  Future<List<ReportSchedule>> schedules() async => [
        for (final item in await _list('/report-schedules'))
          ReportSchedule.fromJson(JsonReader.of(item, 'schedule')),
      ];

  @override
  Future<ReportSchedule> createSchedule(Map<String, Object?> fields) async =>
      ReportSchedule.fromJson(
          await _call('POST', '/report-schedules', body: fields));

  @override
  Future<void> deleteSchedule(String id) =>
      api.request('DELETE', '$_base/report-schedules/${_id(id)}');
}

/// The state of the plan, reports and schedules tabs.
class TyreOps extends ChangeNotifier {
  TyreOps(this.repository);
  final TyreOpsRepository repository;
  static const pageSize = 20;
  static const planMonths = [3, 6, 12];

  OpsLoad planState = OpsLoad.idle;
  PurchasePlan? plan;
  String? planError;
  int months = 6;

  OpsLoad reportsState = OpsLoad.idle;
  final List<TyreReportItem> reports = [];
  int reportsTotal = 0;
  bool loadingMore = false;
  String? reportsError;
  int _reportsGeneration = 0;
  bool get hasMoreReports => reports.length < reportsTotal;

  OpsLoad schedulesState = OpsLoad.idle;
  final List<ReportSchedule> schedules = [];
  String? schedulesError;

  OpsLoad _stateOf(Object e) => e is ApiException && e.isPermissionDenied
      ? OpsLoad.denied
      : OpsLoad.error;
  String _message(Object e) => e is ApiException ? e.readable : '$e';

  Future<int> sync() => repository.sync();

  int _planGeneration = 0;

  Future<void> loadPlan() async {
    final generation = ++_planGeneration;
    planState = OpsLoad.loading;
    planError = null;
    notifyListeners();
    try {
      final found = await repository.plan(months);
      if (generation != _planGeneration) return;
      plan = found;
      planState = OpsLoad.ready;
    } catch (e) {
      if (generation != _planGeneration) return;
      planState = _stateOf(e);
      planError = _message(e);
    }
    notifyListeners();
  }

  Future<void> setMonths(int value) {
    months = value;
    return loadPlan();
  }

  Future<void> loadReports() async {
    final generation = ++_reportsGeneration;
    reportsState = OpsLoad.loading;
    reportsError = null;
    notifyListeners();
    try {
      final page = await repository.reports(offset: 0, limit: pageSize);
      if (generation != _reportsGeneration) return;
      reports
        ..clear()
        ..addAll(page.items);
      reportsTotal = page.total;
      reportsState = OpsLoad.ready;
    } catch (e) {
      if (generation != _reportsGeneration) return;
      reportsState = _stateOf(e);
      reportsError = _message(e);
    }
    notifyListeners();
  }

  Future<void> moreReports() async {
    if (loadingMore || !hasMoreReports) return;
    final generation = _reportsGeneration;
    loadingMore = true;
    notifyListeners();
    try {
      final page =
          await repository.reports(offset: reports.length, limit: pageSize);
      if (generation == _reportsGeneration) {
        reports.addAll(page.items);
        reportsTotal = page.total;
      }
    } catch (e) {
      reportsError = _message(e);
    } finally {
      loadingMore = false;
      notifyListeners();
    }
  }

  Future<TyreReportItem> generate(String kind, String? from, String? to) async {
    final report = await repository.generate(kind, from, to);
    await loadReports();
    return report;
  }

  Future<void> deleteReport(String id) async {
    await repository.deleteReport(id);
    await loadReports();
  }

  Future<void> loadSchedules() async {
    schedulesState = OpsLoad.loading;
    schedulesError = null;
    notifyListeners();
    try {
      final found = await repository.schedules();
      schedules
        ..clear()
        ..addAll(found);
      schedulesState = OpsLoad.ready;
    } catch (e) {
      schedulesState = _stateOf(e);
      schedulesError = _message(e);
    }
    notifyListeners();
  }

  Future<void> createSchedule(Map<String, Object?> fields) async {
    await repository.createSchedule(fields);
    await loadSchedules();
  }

  Future<void> deleteSchedule(String id) async {
    await repository.deleteSchedule(id);
    await loadSchedules();
  }

  void reset() {
    plan = null;
    _planGeneration++;
    planState = reportsState = schedulesState = OpsLoad.idle;
    reports.clear();
    reportsTotal = 0;
    schedules.clear();
    _reportsGeneration++;
    notifyListeners();
  }
}
