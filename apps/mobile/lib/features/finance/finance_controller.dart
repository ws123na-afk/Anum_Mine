import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'finance_depth_controller.dart';
import 'finance_depth_models.dart';
import 'finance_intel_controller.dart';
import 'finance_intel_models.dart';
import 'finance_models.dart';
import 'finance_personal_controller.dart';
import 'finance_personal_repository.dart';
import 'finance_plan_models.dart';
import 'finance_repository.dart';
import 'finance_review_models.dart';
import 'finance_trading_models.dart';

enum FinanceLoad { idle, loading, ready, denied, error }

/// Sections of the phone's Finance screen. One place per job, as on the web
/// (docs/finance.md#finance-shell): Lent and borrowed is part of Debts and
/// the monthly fixed costs part of Bills.
enum FinanceSection {
  overview,
  planner,
  safe,
  transactions,
  suggestions,
  accounts,
  bills,
  debts,
  goals,
  guardian,
  connections,
  settings,
  trading,
  intel,
  execution
}

/// State of the phone's Finance screen (docs/finance.md): the month's summary,
/// a searchable, paged transaction list, accounts, bills, debts and goals.
/// Each part loads and fails on its own, so a failed list keeps the summary.
class FinanceController extends ChangeNotifier {
  FinanceController(this.repository,
      {FinanceIntelRepository? intelRepository,
      FinancePersonalRepository? personalRepository,
      FinanceDepthRepository? depthRepository,
      MoneyPlanner? planner})
      : depth = depthRepository == null
            ? null
            : FinanceDepthController(depthRepository),
        intel = intelRepository == null
            ? null
            : FinanceIntelController(intelRepository),
        personal = personalRepository == null
            ? null
            : FinancePersonalController(personalRepository, planner: planner);

  final FinanceRepository repository;

  /// Personal money (docs/finance.md#lent-and-borrowed): the Money planner,
  /// Lent and borrowed, Fixed costs and the debt payoff card; null when no
  /// source was given, and then those sections are not offered.
  final FinancePersonalController? personal;

  /// Finance settings, the reserve form, a transaction's FX section and the
  /// exception notices (docs/finance.md#finance-settings); null when no
  /// source was given, and then the settings section is not offered.
  final FinanceDepthController? depth;

  /// Whether [section] is offered on this phone.
  bool offers(FinanceSection section) => switch (section) {
        FinanceSection.intel => intel != null,
        FinanceSection.planner => personal != null,
        FinanceSection.settings => depth != null,
        _ => true,
      };

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

  /// The transaction type the list is narrowed to (`uncategorized`), or null.
  String? typeFilter;

  /// Rows no rule recognised, in no income or spending figure; 0 when the
  /// count could not be read (the list still works).
  int uncategorized = 0;
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

  // The last statement import and reconciliation status (on Connections).
  FinanceLoad importState = FinanceLoad.idle;
  ImportRecord? lastImport;
  String? importError;
  FinanceLoad reconState = FinanceLoad.idle;
  ReconStatus? recon;
  String? reconError;

  // The suggestion queue.
  FinanceLoad suggestionsState = FinanceLoad.idle;
  final List<Suggestion> suggestions = [];
  int suggestionsTotal = 0;
  List<String> suggestionsUnavailable = const [];
  String? suggestionsError;
  bool loadingMoreSuggestions = false;

  /// The suggestion being accepted or dismissed, so its buttons wait.
  String? deciding;

  bool get hasMoreSuggestions => suggestions.length < suggestionsTotal;

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
      case FinanceSection.suggestions:
        await loadSuggestions();
      case FinanceSection.bills:
        await Future.wait([
          loadItems('bills'),
          if (personal != null) personal!.loadCommitments()
        ]);
      case FinanceSection.debts:
        await Future.wait([
          loadItems('debts'),
          if (personal != null) personal!.loadPayoff(),
          if (personal != null) personal!.loadLoans(),
          if (personal != null) personal!.loadLoanSuggestions(),
        ]);
      case FinanceSection.planner:
        break;
      case FinanceSection.goals:
        await loadItems('goals');
      case FinanceSection.accounts:
        await loadAccounts();
      case FinanceSection.safe:
        await Future.wait([
          loadSafe(),
          if (personal != null) personal!.loadSalary(),
          if (personal != null) personal!.loadIncome(),
        ]);
      case FinanceSection.guardian:
        await loadGuardian();
      case FinanceSection.connections:
        await Future.wait(
            [loadConnections(), loadLastImport(), loadReconciliation()]);
      case FinanceSection.settings:
        await Future.wait([
          if (depth != null) depth!.load(),
          loadAccounts(),
          if (personal != null) personal!.loadSalary(),
        ]);
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

  /// Shows only the uncategorized rows, or every row again.
  Future<void> showUncategorized(bool only) async {
    typeFilter = only ? 'uncategorized' : null;
    await loadTransactions();
  }

  Future<void> _countUncategorized() async {
    try {
      uncategorized = await repository.uncategorizedCount();
    } catch (_) {
      // The notice is a hint; the list says what failed on its own.
      uncategorized = 0;
    }
  }

  Future<void> loadTransactions() async {
    transactionsState = FinanceLoad.loading;
    transactionsError = null;
    notifyListeners();
    try {
      final found = await Future.wait([
        repository.transactions(
            query: query, offset: 0, limit: pageSize, type: typeFilter),
        _countUncategorized(),
      ]);
      final page = found.first as TransactionPage;
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
          query: query,
          offset: transactions.length,
          limit: pageSize,
          type: typeFilter);
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

  /// Files a row as [type] (under [category] when chosen), then reloads the
  /// list, the uncategorized count and the month's figures.
  Future<String?> fileTransaction(FinanceTransaction row,
          {required String type, String? category}) =>
      _change(
          () => repository.fileTransaction(row.id,
              type: type, category: category),
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

  Future<void> loadLastImport() async {
    importState = FinanceLoad.loading;
    importError = null;
    notifyListeners();
    try {
      lastImport = await repository.lastImport();
      importState = FinanceLoad.ready;
    } catch (error) {
      importState = _failed(error);
      importError = _reason(error);
    }
    notifyListeners();
  }

  Future<void> loadReconciliation() async {
    reconState = FinanceLoad.loading;
    reconError = null;
    notifyListeners();
    try {
      recon = await repository.reconciliation();
      reconState = FinanceLoad.ready;
    } catch (error) {
      reconState = _failed(error);
      reconError = _reason(error);
    }
    notifyListeners();
  }

  Future<void> loadSuggestions() async {
    suggestionsState = FinanceLoad.loading;
    suggestionsError = null;
    notifyListeners();
    try {
      final page = await repository.suggestions(offset: 0, limit: pageSize);
      suggestions
        ..clear()
        ..addAll(page.items);
      suggestionsTotal = page.total;
      suggestionsUnavailable = page.unavailable;
      suggestionsState = FinanceLoad.ready;
    } catch (error) {
      suggestionsState = _failed(error);
      suggestionsError = _reason(error);
    }
    notifyListeners();
  }

  /// The next page; a failure keeps the list and says why.
  Future<void> loadMoreSuggestions() async {
    if (loadingMoreSuggestions || !hasMoreSuggestions) return;
    loadingMoreSuggestions = true;
    suggestionsError = null;
    notifyListeners();
    try {
      final page = await repository.suggestions(
          offset: suggestions.length, limit: pageSize);
      suggestions.addAll(page.items);
      suggestionsTotal = page.total;
    } catch (error) {
      suggestionsError = _reason(error);
    } finally {
      loadingMoreSuggestions = false;
      notifyListeners();
    }
  }

  /// Accepts or dismisses [item], then reloads the queue (and the month,
  /// whose figures an accept can change). Returns the rows changed (null
  /// when none) or the server's refusal.
  Future<({String? error, int? changed})> decideSuggestion(Suggestion item,
      {required bool accept}) async {
    if (deciding != null) return (error: null, changed: null);
    deciding = item.key;
    notifyListeners();
    try {
      int? changed;
      if (accept) {
        changed =
            (await repository.acceptSuggestion(item.key, item.payloadHash))
                .changed;
      } else {
        await repository.dismissSuggestion(item.key);
      }
      deciding = null;
      await Future.wait([loadSuggestions(), if (accept) loadSummary()]);
      return (error: null, changed: changed);
    } catch (error) {
      deciding = null;
      // A suggestion that changed or was decided elsewhere leaves the list.
      await loadSuggestions();
      return (error: _reason(error), changed: null);
    }
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
    typeFilter = null;
    uncategorized = 0;
    section = FinanceSection.overview;
    lastImport = null;
    recon = null;
    suggestions.clear();
    suggestionsTotal = 0;
    suggestionsUnavailable = const [];
    deciding = null;
    importState = reconState = suggestionsState = FinanceLoad.idle;
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
    personal?.reset();
    depth?.reset();
    notifyListeners();
  }

  @override
  void dispose() {
    personal?.dispose();
    depth?.dispose();
    super.dispose();
  }
}
