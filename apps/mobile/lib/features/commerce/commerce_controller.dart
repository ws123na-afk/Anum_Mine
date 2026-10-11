import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'commerce_models.dart';
import 'commerce_repository.dart';

enum CommerceLoad { idle, loading, ready, denied, error }

/// State of the phone's Commerce screen: a summary for a period and a paged
/// order list. Each part loads and fails on its own, so a failed order page
/// keeps the summary on screen.
class CommerceController extends ChangeNotifier {
  CommerceController(this.repository, {DateTime Function()? clock})
      : _clock = clock ?? DateTime.now;

  final CommerceRepository repository;
  final DateTime Function() _clock;
  static const pageSize = 20;
  static const periods = ['today', 'yesterday', 'last_7_days', 'last_30_days'];

  String period = 'today';
  String lang = 'en';
  CommerceLoad summaryState = CommerceLoad.idle;
  CommerceDashboard? dashboard;
  String? summaryText;
  String? summaryError;

  String? statusFilter = 'open';
  CommerceLoad ordersState = CommerceLoad.idle;
  final List<CommerceOrder> orders = [];
  int ordersTotal = 0;
  bool loadingMore = false;
  String? ordersError;

  bool get hasMoreOrders => orders.length < ordersTotal;

  Future<void> load() => Future.wait([loadSummary(), loadOrders()]);

  Future<void> setPeriod(String value) async {
    if (value == period) return;
    period = value;
    await loadSummary();
  }

  Future<void> setStatus(String? value) async {
    statusFilter = value;
    await loadOrders();
  }

  Future<void> loadSummary() async {
    summaryState = CommerceLoad.loading;
    summaryError = null;
    notifyListeners();
    try {
      final range = periodRange(period, _clock());
      final results = await Future.wait([
        repository.dashboard(range.from, range.to),
        repository.summary(period, lang),
      ]);
      dashboard = results[0] as CommerceDashboard;
      summaryText = (results[1] as SalesSummary).text;
      summaryState = CommerceLoad.ready;
    } on ApiException catch (error) {
      summaryState =
          error.isPermissionDenied ? CommerceLoad.denied : CommerceLoad.error;
      summaryError = error.message;
    } catch (error) {
      summaryState = CommerceLoad.error;
      summaryError = '$error';
    }
    notifyListeners();
  }

  Future<void> loadOrders() async {
    ordersState = CommerceLoad.loading;
    ordersError = null;
    notifyListeners();
    try {
      final page = await repository.orders(
          status: statusFilter, offset: 0, limit: pageSize);
      orders
        ..clear()
        ..addAll(page.items);
      ordersTotal = page.total;
      ordersState = CommerceLoad.ready;
    } on ApiException catch (error) {
      ordersState =
          error.isPermissionDenied ? CommerceLoad.denied : CommerceLoad.error;
      ordersError = error.message;
    } catch (error) {
      ordersState = CommerceLoad.error;
      ordersError = '$error';
    }
    notifyListeners();
  }

  Future<void> loadMoreOrders() async {
    if (loadingMore || !hasMoreOrders) return;
    loadingMore = true;
    notifyListeners();
    try {
      final page = await repository.orders(
          status: statusFilter, offset: orders.length, limit: pageSize);
      orders.addAll(page.items);
      ordersTotal = page.total;
    } on ApiException catch (error) {
      ordersError = error.message;
    } catch (error) {
      ordersError = '$error';
    } finally {
      loadingMore = false;
      notifyListeners();
    }
  }

  /// Forget everything (sign-out or workspace switch).
  void reset() {
    dashboard = null;
    summaryText = null;
    orders.clear();
    ordersTotal = 0;
    summaryState = ordersState = CommerceLoad.idle;
    notifyListeners();
  }
}
