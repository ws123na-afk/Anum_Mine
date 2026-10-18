import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'finance_intel_controller.dart';
import 'finance_intel_models.dart';
import 'finance_models.dart';
import 'finance_plan_models.dart';
import 'finance_repository.dart';
import 'finance_trading_models.dart';

enum FinanceLoad { idle, loading, ready, denied, error }

/// Sections of the phone's Finance screen.
enum FinanceSection {
  overview,
  safe,
  transactions,
  accounts,
  bills,
  debts,
  goals,
  guardian,
  connections,
  trading,
  intel,
  execution
}

/// State of the phone's Finance screen (docs/finance.md): the month's summary,
/// a searchable, paged transaction list, accounts, bills, debts and goals.
/// Each part loads and fails on its own, so a failed list keeps the summary.
class FinanceController extends ChangeNotifier {
  FinanceController(this.repository, {FinanceIntelRepository? intelRepository})
      : intel = intelRepository == null
            ? null
            : FinanceIntelController(intelRepository);

  final FinanceRepository repository;

  /// The trading intelligence section (docs/finance-trading-intel.md); null
  /// when no intel source was given, and then the section is not offered.
  final FinanceIntelController? intel;
  static const pageSize = 25;

  FinanceSection section = FinanceSection.overview;

  FinanceLoad summaryState = FinanceLoad.idle;
  FinanceSummary? summary;
  String? summaryError;

  FinanceLoad accountsState = FinanceLoad.idle;
  final List<FinanceAccount> accounts = [];

  String query = '';
  FinanceLoad transactionsState = FinanceLoad.idle;
  final List<FinanceTransaction> transactions = [];
  int transactionsTotal = 0;
  bool loadingMore = false;
  String? transactionsError;

  final Map<String, List<FinanceItem>> items = {};
  final Map<String, FinanceLoad> itemStates = {};
  String? itemsError;

  // Stages 3 to 5.
  FinanceLoad safeState = FinanceLoad.idle;
  SafeToSpend? safe;
  String? safeError;
  FinanceLoad guardianState = FinanceLoad.idle;
  GuardianView? guardian;
  List<GuardianAlert> alerts = const [];
  String? guardianError;
  FinanceLoad connectionsState = FinanceLoad.idle;
  final List<FinanceConnection> connections = [];
  String? connectionsError;

  /// The last change's outcome, shown once by the screen.
  String? message;

  bool get hasMoreTransactions => transactions.length < transactionsTotal;

  Future<void> load() => Future.wait([loadSummary(), loadAccounts()]);

  // Stages 6 to 9: SIMULATED portfolios, requests waiting for approval, the
  // kill switch and live use (off).
  FinanceLoad tradingState = FinanceLoad.idle;
  List<PortfolioSummary> portfolios = const [];
  List<TradeIntent> pendingIntents = const [];
  String? tradingError;
  FinanceLoad executionLoad = FinanceLoad.idle;
  ExecutionState? execution;
  LiveState? live;
  String? executionError;

  Future<void> select(FinanceSection next) async {
    section = next;
    notifyListeners();
    switch (next) {
      case FinanceSection.transactions:
        if (transactionsState == FinanceLoad.idle) await loadTransactions();
      case FinanceSection.bills:
        await loadItems('bills');
      case FinanceSection.debts:
        await loadItems('debts');
      case FinanceSection.goals:
        await loadItems('goals');
      case FinanceSection.accounts:
        await loadAccounts();
      case FinanceSection.safe:
        await loadSafe();
      case FinanceSection.guardian:
        await loadGuardian();
      case FinanceSection.connections:
        await loadConnections();
      case FinanceSection.trading:
        await loadTrading();
      case FinanceSection.intel:
        await intel?.load();
      case FinanceSection.execution:
        await loadExecution();
      case FinanceSection.overview:
        break;
    }
  }

  FinanceLoad _failed(Object error) =>
      error is ApiException && error.isPermissionDenied
          ? FinanceLoad.denied
          : FinanceLoad.error;

  String _reason(Object error) =>
      error is ApiException ? error.message : '$error';

  Future<void> loadSummary() async {
    summaryState = FinanceLoad.loading;
    summaryError = null;
    notifyListeners();
    try {
      summary = await repository.summary();
      summaryState = FinanceLoad.ready;
    } catch (error) {
      summaryState = _failed(error);
      summaryError = _reason(error);
    }
    notifyListeners();
  }

  Future<void> loadAccounts() async {
    accountsState = FinanceLoad.loading;
    notifyListeners();
    try {
      final found = await repository.accounts();
      accounts
        ..clear()
        ..addAll(found);
      accountsState = FinanceLoad.ready;
    } catch (error) {
      accountsState = _failed(error);
    }
    notifyListeners();
  }

  Future<void> search(String value) async {
    query = value.trim();
    await loadTransactions();
  }

  Future<void> loadTransactions() async {
    transactionsState = FinanceLoad.loading;
    transactionsError = null;
    notifyListeners();
    try {
      final page = await repository.transactions(
          query: query, offset: 0, limit: pageSize);
      transactions
        ..clear()
        ..addAll(page.items);
      transactionsTotal = page.total;
      transactionsState = FinanceLoad.ready;
    } catch (error) {
      transactionsState = _failed(error);
      transactionsError = _reason(error);
    }
    notifyListeners();
  }

  Future<void> loadMoreTransactions() async {
    if (loadingMore || !hasMoreTransactions) return;
    loadingMore = true;
    notifyListeners();
    try {
      final page = await repository.transactions(
          query: query, offset: transactions.length, limit: pageSize);
      transactions.addAll(page.items);
      transactionsTotal = page.total;
    } catch (error) {
      transactionsError = _reason(error);
    } finally {
      loadingMore = false;
      notifyListeners();
    }
  }

  Future<void> loadItems(String kind) async {
    itemStates[kind] = FinanceLoad.loading;
    itemsError = null;
    notifyListeners();
    try {
      items[kind] = await repository.items(kind);
      itemStates[kind] = FinanceLoad.ready;
    } catch (error) {
      itemStates[kind] = _failed(error);
      itemsError = _reason(error);
    }
    notifyListeners();
  }

  /// Runs one change, then reloads what it touched. Returns null on success,
  /// else the reason (the server's message).
  Future<String?> _change(
      Future<void> Function() action, Future<void> Function() reload) async {
    try {
      await action();
      await Future.wait([reload(), loadSummary()]);
      return null;
    } catch (error) {
      return _reason(error);
    }
  }

  Future<String?> addAccount(
          {required String name, required String kind, String? last4}) =>
      _change(
          () => repository.createAccount(name: name, kind: kind, last4: last4),
          loadAccounts);

  Future<String?> deleteAccount(String id) =>
      _change(() => repository.deleteAccount(id), loadAccounts);

  Future<String?> addTransaction(
          {required String accountId,
          required String postedOn,
          required String amount,
          required String description}) =>
      _change(
          () => repository.createTransaction(
              accountId: accountId,
              postedOn: postedOn,
              amount: amount,
              description: description),
          loadTransactions);

  Future<String?> deleteTransaction(String id) =>
      _change(() => repository.deleteTransaction(id), loadTransactions);

  Future<String?> addBill(
          {required String name,
          required String amount,
          required String dueOn}) =>
      _change(
          () => repository.createBill(name: name, amount: amount, dueOn: dueOn),
          () => loadItems('bills'));

  Future<String?> deleteItem(String kind, String id) =>
      _change(() => repository.deleteItem(kind, id), () => loadItems(kind));

  Future<void> loadSafe() async {
    safeState = FinanceLoad.loading;
    safeError = null;
    notifyListeners();
    try {
      safe = await repository.safeToSpend();
      safeState = FinanceLoad.ready;
    } catch (error) {
      safeState = _failed(error);
      safeError = _reason(error);
    }
    notifyListeners();
  }

  Future<void> loadGuardian() async {
    guardianState = FinanceLoad.loading;
    guardianError = null;
    notifyListeners();
    try {
      final found = await Future.wait(
          [repository.guardian(), repository.guardianAlerts()]);
      guardian = found[0] as GuardianView;
      alerts = found[1] as List<GuardianAlert>;
      guardianState = FinanceLoad.ready;
    } catch (error) {
      guardianState = _failed(error);
      guardianError = _reason(error);
    }
    notifyListeners();
  }

  Future<void> loadConnections() async {
    connectionsState = FinanceLoad.loading;
    connectionsError = null;
    notifyListeners();
    try {
      final found = await repository.connections();
      connections
        ..clear()
        ..addAll(found);
      connectionsState = FinanceLoad.ready;
    } catch (error) {
      connectionsState = _failed(error);
      connectionsError = _reason(error);
    }
    notifyListeners();
  }

  /// Confirms exactly the change shown, with its payload hash. Agents never
  /// reach this: only the person's own tap does.
  Future<String?> confirmChange(GuardianChange change) => _change(
      () => repository.confirmChange(change.id, change.payloadHash),
      loadGuardian);

  Future<String?> rejectChange(GuardianChange change) =>
      _change(() => repository.rejectChange(change.id), loadGuardian);

  Future<String?> syncConnection(String id) =>
      _change(() => repository.syncConnection(id), loadConnections);

  Future<void> loadTrading() async {
    tradingState = FinanceLoad.loading;
    tradingError = null;
    notifyListeners();
    try {
      final found = await Future.wait(
          [repository.portfolios(), repository.pendingIntents()]);
      portfolios = found[0] as List<PortfolioSummary>;
      pendingIntents = (found[1] as List<TradeIntent>)
          .where((i) => i.mode == 'paper' || i.mode == 'shadow')
          .toList();
      tradingState = FinanceLoad.ready;
    } catch (error) {
      tradingState = _failed(error);
      tradingError = _reason(error);
    }
    notifyListeners();
  }

  Future<void> loadExecution() async {
    executionLoad = FinanceLoad.loading;
    executionError = null;
    notifyListeners();
    try {
      final found = await Future.wait(
          [repository.executionState(), repository.liveState()]);
      execution = found[0] as ExecutionState;
      live = found[1] as LiveState;
      executionLoad = FinanceLoad.ready;
    } catch (error) {
      executionLoad = _failed(error);
      executionError = _reason(error);
    }
    notifyListeners();
  }

  /// Approves exactly the request shown, with its payload hash, once. Agents
  /// never reach this: only the person's own tap does.
  Future<String?> approveIntent(TradeIntent intent) => _change(
      () => repository.approveIntent(intent.id, intent.payloadHash),
      loadTrading);

  Future<String?> rejectIntent(TradeIntent intent) =>
      _change(() => repository.rejectIntent(intent.id), loadTrading);

  Future<String?> setKillSwitch(bool engaged) =>
      _change(() => repository.setKillSwitch(engaged), loadExecution);

  Future<String?> reviewIncidents() =>
      _change(repository.reviewIncidents, loadExecution);

  /// Forget everything (sign-out or workspace switch).
  void reset() {
    summary = null;
    accounts.clear();
    transactions.clear();
    transactionsTotal = 0;
    items.clear();
    itemStates.clear();
    query = '';
    section = FinanceSection.overview;
    safe = null;
    guardian = null;
    alerts = const [];
    connections.clear();
    summaryState = accountsState = transactionsState = FinanceLoad.idle;
    safeState = guardianState = connectionsState = FinanceLoad.idle;
    portfolios = const [];
    pendingIntents = const [];
    execution = null;
    live = null;
    tradingState = executionLoad = FinanceLoad.idle;
    notifyListeners();
  }
}
