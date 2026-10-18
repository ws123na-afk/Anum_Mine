import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'finance_controller.dart' show FinanceLoad;
import 'finance_intel_models.dart';

/// State of the trading intelligence section on the phone
/// (docs/finance-trading-intel.md#dashboard). Each part loads and fails on its
/// own, so a failed decision list keeps the overview. Read only, apart from
/// the kill switch, which always asks first (in the view).
class FinanceIntelController extends ChangeNotifier {
  FinanceIntelController(this.repository);

  final FinanceIntelRepository repository;
  static const pageSize = 10;
  static const runPageSize = 5;
  static const equityDays = 90;

  FinanceLoad overviewLoad = FinanceLoad.idle;
  IntelOverview? overview;
  String? overviewError;

  String? stateFilter;
  FinanceLoad strategiesLoad = FinanceLoad.idle;
  IntelPage<IntelStrategy>? strategies;
  String? strategiesError;

  IntelStrategy? selected;
  FinanceLoad runsLoad = FinanceLoad.idle;
  IntelPage<IntelRun>? runs;
  String? runsError;

  String? agentFilter;
  FinanceLoad decisionsLoad = FinanceLoad.idle;
  IntelPage<IntelDecision>? decisions;
  String? decisionsError;

  final Map<String, FinanceLoad> equityLoad = {};
  final Map<String, EquityCurve> equity = {};
  final Map<String, String> equityError = {};

  bool switching = false;

  FinanceLoad _failed(Object error) =>
      error is ApiException && error.isPermissionDenied
          ? FinanceLoad.denied
          : FinanceLoad.error;

  String _reason(Object error) =>
      error is ApiException ? error.message : '$error';

  Future<void> load() => Future.wait(
      [loadOverview(), loadStrategies(offset: 0), loadDecisions(offset: 0)]);

  Future<void> loadOverview() async {
    overviewLoad = FinanceLoad.loading;
    overviewError = null;
    notifyListeners();
    try {
      overview = await repository.overview();
      overviewLoad = FinanceLoad.ready;
    } catch (error) {
      overviewLoad = _failed(error);
      overviewError = _reason(error);
    }
    notifyListeners();
    for (final p in overview?.portfolios ?? const <IntelPortfolio>[]) {
      await loadEquity(p.id);
    }
  }

  Future<void> loadStrategies({int? offset}) async {
    strategiesLoad = FinanceLoad.loading;
    strategiesError = null;
    notifyListeners();
    try {
      strategies = await repository.strategies(
          state: stateFilter,
          offset: offset ?? strategies?.offset ?? 0,
          limit: pageSize);
      strategiesLoad = FinanceLoad.ready;
    } catch (error) {
      strategiesLoad = _failed(error);
      strategiesError = _reason(error);
    }
    notifyListeners();
  }

  Future<void> filterState(String? state) async {
    stateFilter = state;
    selected = null;
    runs = null;
    await loadStrategies(offset: 0);
  }

  Future<void> select(IntelStrategy? strategy) async {
    selected = selected?.id == strategy?.id ? null : strategy;
    runs = null;
    notifyListeners();
    if (selected != null) await loadRuns(offset: 0);
  }

  Future<void> loadRuns({int? offset}) async {
    final strategy = selected;
    if (strategy == null) return;
    runsLoad = FinanceLoad.loading;
    runsError = null;
    notifyListeners();
    try {
      runs = await repository.runs(strategy.id,
          offset: offset ?? runs?.offset ?? 0, limit: runPageSize);
      runsLoad = FinanceLoad.ready;
    } catch (error) {
      runsLoad = _failed(error);
      runsError = _reason(error);
    }
    notifyListeners();
  }

  Future<void> loadDecisions({int? offset}) async {
    decisionsLoad = FinanceLoad.loading;
    decisionsError = null;
    notifyListeners();
    try {
      decisions = await repository.decisions(
          agent: agentFilter,
          offset: offset ?? decisions?.offset ?? 0,
          limit: pageSize);
      decisionsLoad = FinanceLoad.ready;
    } catch (error) {
      decisionsLoad = _failed(error);
      decisionsError = _reason(error);
    }
    notifyListeners();
  }

  Future<void> filterAgent(String? agent) async {
    agentFilter = agent;
    await loadDecisions(offset: 0);
  }

  Future<void> loadEquity(String portfolioId) async {
    equityLoad[portfolioId] = FinanceLoad.loading;
    equityError.remove(portfolioId);
    notifyListeners();
    try {
      equity[portfolioId] =
          await repository.equity(portfolioId, days: equityDays);
      equityLoad[portfolioId] = FinanceLoad.ready;
    } catch (error) {
      equityLoad[portfolioId] = _failed(error);
      equityError[portfolioId] = _reason(error);
    }
    notifyListeners();
  }

  /// Engages (true) or releases the person's kill switch. Returns an error
  /// to show, or null when it worked.
  Future<String?> setStopped(bool stopped) async {
    switching = true;
    notifyListeners();
    try {
      await repository.setKillSwitch(stopped);
      await loadOverview();
      return null;
    } catch (error) {
      return _reason(error);
    } finally {
      switching = false;
      notifyListeners();
    }
  }
}
