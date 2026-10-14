import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'finance_models.dart';
import 'finance_repository.dart';

enum FinanceLoad { idle, loading, ready, denied, error }

/// Sections of the phone's Finance screen.
enum FinanceSection { overview, transactions, accounts, bills, debts, goals }

/// State of the phone's Finance screen (docs/finance.md): the month's summary,
/// a searchable, paged transaction list, accounts, bills, debts and goals.
/// Each part loads and fails on its own, so a failed list keeps the summary.
class FinanceController extends ChangeNotifier {
  FinanceController(this.repository);

  final FinanceRepository repository;
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

  /// The last change's outcome, shown once by the screen.
  String? message;

  bool get hasMoreTransactions => transactions.length < transactionsTotal;

  Future<void> load() => Future.wait([loadSummary(), loadAccounts()]);

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
    summaryState = accountsState = transactionsState = FinanceLoad.idle;
    notifyListeners();
  }
}
