import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'finance_controller.dart' show FinanceLoad;
import 'finance_intel_models.dart';

/// State of the trading intelligence section on the phone
/// (docs/finance-trading-intel.md#dashboard). Each part loads and fails on its
/// own, so a failed decision list keeps the overview. Changes (the kill
/// switch, a new strategy, a lifecycle move, a Supervisor cycle, the pause)
/// always ask first in the view; none approves an order or trades live.
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

  // Actions: what a new strategy may start from, and whether one is running.
  FinanceLoad setupLoad = FinanceLoad.idle;
  List<StrategyRule> rules = const [];
  List<IntelInstrument> instruments = const [];

  /// Every instrument, also those without bars yet (Market data).
  List<IntelInstrument> allInstruments = const [];

  /// Picks a bars file; the view's default uses the system file picker.
  BarsFilePicker? pickBars;
  String? setupError;
  bool acting = false;

  /// The newest Supervisor cycle run from the phone.
  IntelCycle? lastCycle;

  FinanceLoad reportsLoad = FinanceLoad.idle;
  IntelPage<DailyReportItem>? reports;
  String? reportsError;
  static const reportPageSize = 10;

  FinanceLoad _failed(Object error) =>
      error is ApiException && error.isPermissionDenied
          ? FinanceLoad.denied
          : FinanceLoad.error;

  String _reason(Object error) =>
      error is ApiException ? error.message : '$error';

  Future<void> load() => Future.wait([
        loadOverview(),
        loadStrategies(offset: 0),
        loadDecisions(offset: 0),
        loadReports(offset: 0),
        loadSetup(),
      ]);

  /// The rules and the instruments with stored bars, for "New strategy".
  Future<void> loadSetup() async {
    setupLoad = FinanceLoad.loading;
    setupError = null;
    notifyListeners();
    try {
      final found = await Future.wait([
        repository.rules(),
        repository.instruments(),
      ]);
      rules = found[0] as List<StrategyRule>;
      allInstruments = found[1] as List<IntelInstrument>;
      instruments = [
        for (final i in allInstruments)
          if (i.bars > 0) i,
      ];
      setupLoad = FinanceLoad.ready;
    } catch (error) {
      setupLoad = _failed(error);
      setupError = _reason(error);
    }
    notifyListeners();
  }

  Future<void> loadReports({int? offset}) async {
    reportsLoad = FinanceLoad.loading;
    reportsError = null;
    notifyListeners();
    try {
      reports = await repository.dailyReports(
        offset: offset ?? reports?.offset ?? 0,
        limit: reportPageSize,
      );
      reportsLoad = FinanceLoad.ready;
    } catch (error) {
      reportsLoad = _failed(error);
      reportsError = _reason(error);
    }
    notifyListeners();
  }

  Future<DailyReport> report(String id) => repository.dailyReport(id);

  /// Runs one change, then reloads what it touched. Returns the server's
  /// error text to show, or null when it worked.
  Future<String?> _act(Future<void> Function() work) async {
    acting = true;
    notifyListeners();
    try {
      await work();
      return null;
    } catch (error) {
      return _reason(error);
    } finally {
      acting = false;
      notifyListeners();
    }
  }

  Future<String?> createStrategy({
    required String key,
    required List<String> instrumentIds,
    required String reason,
  }) =>
      _act(() async {
        await repository.createStrategy(
          key: key,
          instrumentIds: instrumentIds,
          reason: reason,
        );
        await Future.wait([loadOverview(), loadStrategies(offset: 0)]);
      });

  Future<String?> move(
    IntelStrategy strategy, {
    required String state,
    required String reason,
  }) =>
      _act(() async {
        await repository.moveStrategy(strategy.id,
            state: state, reason: reason);
        selected = null;
        runs = null;
        await Future.wait([loadOverview(), loadStrategies()]);
      });

  Future<String?> runCycle(String phase) => _act(() async {
        lastCycle = await repository.runSupervisor(phase);
        await Future.wait([
          loadOverview(),
          loadStrategies(),
          loadDecisions(offset: 0),
          loadReports(offset: 0),
        ]);
      });

  Future<String?> createInstrument(
          {required String symbol,
          required String assetClass,
          required String venue,
          required String currency,
          String? listedOn}) =>
      _act(() async {
        await repository.createInstrument(
            symbol: symbol,
            assetClass: assetClass,
            venue: venue,
            currency: currency,
            listedOn: listedOn);
        await loadSetup();
      });

  /// Imports one bars file. Returns (error, what the import did).
  Future<(String?, BarsImport?)> importBars(
      IntelInstrument instrument, BarsFile file) async {
    BarsImport? done;
    final error = await _act(() async {
      done = await repository.importBars(instrument.id,
          payload: file.text ?? '', source: file.name);
      await loadSetup();
    });
    return (error, done);
  }

  /// A SIMULATED evaluation. Returns (error, verdict).
  Future<(String?, String?)> evaluate(IntelStrategy strategy,
      {required String kind}) async {
    String? verdict;
    final error = await _act(() async {
      verdict = await repository.evaluate(strategy.id, kind: kind);
      if (selected?.id == strategy.id) await loadRuns(offset: 0);
    });
    return (error, verdict);
  }

  Future<String?> choosePortfolio(IntelStrategy strategy, String portfolioId) =>
      _act(() async {
        await repository.choosePortfolio(strategy.id, portfolioId);
        await loadStrategies();
      });

  /// Suspends every active paper strategy. Returns (error, how many paused).
  Future<(String?, int)> pauseAll(String reason) async {
    var paused = 0;
    final error = await _act(() async {
      paused = await repository.pauseAll(reason);
      await Future.wait([
        loadOverview(),
        loadStrategies(),
        loadDecisions(offset: 0),
      ]);
    });
    return (error, paused);
  }

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
        limit: pageSize,
      );
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
      runs = await repository.runs(
        strategy.id,
        offset: offset ?? runs?.offset ?? 0,
        limit: runPageSize,
      );
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
        limit: pageSize,
      );
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
      equity[portfolioId] = await repository.equity(
        portfolioId,
        days: equityDays,
      );
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
