import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'tyre_models.dart';
import 'tyre_repository.dart';

/// One section's state: loading, failed (with the API's message), denied.
class TyreSection<T> {
  const TyreSection({
    this.items = const [],
    this.total = 0,
    this.loading = false,
    this.error,
    this.denied = false,
  });
  final List<T> items;
  final int total;
  final bool loading, denied;
  final String? error;
  bool get hasMore => items.length < total;
}

/// Tyre planning on the phone: dashboard, tyres due by month, alerts.
class TyreController extends ChangeNotifier {
  TyreController(this.repository, {DateTime Function()? clock})
      : _clock = clock ?? DateTime.now {
    final now = _clock();
    month = DateTime(now.year, now.month);
  }

  final TyreRepository repository;
  final DateTime Function() _clock;

  TyreAccess? access;
  TyreDashboard? dashboard;
  bool loading = false, denied = false;
  String? error;
  late DateTime month;
  String query = '';
  TyreSection<TyreForecast> due = const TyreSection();
  TyreSection<TyreAlert> alerts = const TyreSection();
  String? acting;
  String? notice;

  String get monthKey =>
      '${month.year.toString().padLeft(4, '0')}-${month.month.toString().padLeft(2, '0')}';

  String _message(Object error) =>
      error is ApiException ? error.message : 'Check your connection.';

  /// Forget everything (sign-out or a workspace switch).
  void reset() {
    access = null;
    dashboard = null;
    loading = denied = false;
    error = notice = acting = null;
    query = '';
    due = const TyreSection();
    alerts = const TyreSection();
    final now = _clock();
    month = DateTime(now.year, now.month);
    notifyListeners();
  }

  Future<void> load() async {
    loading = true;
    error = null;
    notifyListeners();
    try {
      access = await repository.access();
      denied = false;
      if (access!.synced) {
        dashboard = await repository.dashboard();
        await Future.wait([loadDue(), loadAlerts()]);
      }
    } on ApiException catch (e) {
      denied = e.isPermissionDenied;
      error = e.message;
    } on Object catch (e) {
      error = _message(e);
    } finally {
      loading = false;
      notifyListeners();
    }
  }

  Future<void> loadDue({bool more = false}) async {
    due = TyreSection(
      items: more ? due.items : const [],
      total: due.total,
      loading: true,
    );
    notifyListeners();
    try {
      final page = await repository.due(
        month: monthKey,
        query: query,
        offset: more ? due.items.length : 0,
      );
      due = TyreSection(
        items: [if (more) ...due.items, ...page.items],
        total: page.total,
      );
    } on ApiException catch (e) {
      due = TyreSection(
        items: due.items,
        error: e.message,
        denied: e.isPermissionDenied,
      );
    } on Object catch (e) {
      due = TyreSection(items: due.items, error: _message(e));
    }
    notifyListeners();
  }

  Future<void> shiftMonth(int delta) {
    month = DateTime(month.year, month.month + delta);
    return loadDue();
  }

  Future<void> search(String value) {
    query = value;
    return loadDue();
  }

  Future<void> loadAlerts({bool more = false}) async {
    alerts = TyreSection(
      items: more ? alerts.items : const [],
      total: alerts.total,
      loading: true,
    );
    notifyListeners();
    try {
      final page = await repository.alerts(
        offset: more ? alerts.items.length : 0,
      );
      alerts = TyreSection(
        items: [if (more) ...alerts.items, ...page.items],
        total: page.total,
      );
    } on ApiException catch (e) {
      alerts = TyreSection(
        items: alerts.items,
        error: e.message,
        denied: e.isPermissionDenied,
      );
    } on Object catch (e) {
      alerts = TyreSection(items: alerts.items, error: _message(e));
    }
    notifyListeners();
  }

  Future<void> acknowledge(TyreAlert alert) async {
    acting = alert.id;
    notice = null;
    notifyListeners();
    try {
      final updated = await repository.acknowledge(alert.id);
      alerts = TyreSection(
        items: [
          for (final item in alerts.items)
            item.id == updated.id ? updated : item,
        ],
        total: alerts.total,
      );
    } on Object catch (e) {
      notice = _message(e);
    } finally {
      acting = null;
      notifyListeners();
    }
  }
}
