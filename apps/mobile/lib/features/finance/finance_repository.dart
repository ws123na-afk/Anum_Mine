import '../../data/api_client.dart';
import '../../data/api_models.dart';
import 'finance_models.dart';

/// Finance over the live API (docs/finance.md). Every record is the caller's
/// own; the server checks `finance:read` and `finance:manage` on each call.
abstract interface class FinanceRepository {
  Future<FinanceSummary> summary({String? month});
  Future<List<FinanceAccount>> accounts();
  Future<FinanceAccount> createAccount(
      {required String name,
      required String kind,
      String? last4,
      String openingBalance = '0'});
  Future<void> deleteAccount(String id);
  Future<TransactionPage> transactions(
      {String? query, required int offset, int limit = 25});
  Future<void> createTransaction(
      {required String accountId,
      required String postedOn,
      required String amount,
      required String description});
  Future<void> deleteTransaction(String id);
  Future<List<FinanceItem>> items(String kind);
  Future<void> createBill(
      {required String name, required String amount, required String dueOn});
  Future<void> deleteItem(String kind, String id);
}

class ApiFinanceRepository implements FinanceRepository {
  ApiFinanceRepository(this.api);
  final AnumApiClient api;
  static const base = '/api/v1/finance';

  String _q(Map<String, String?> values) {
    final kept = {
      for (final e in values.entries)
        if (e.value != null && e.value!.isNotEmpty) e.key: e.value!
    };
    return kept.isEmpty ? '' : '?${Uri(queryParameters: kept).query}';
  }

  /// List answers arrive wrapped as `{"data": [...]}` by the transport.
  List<T> _list<T>(JsonMap body, T Function(JsonReader) read) =>
      JsonReader.of(body).optObjects('data', read);

  @override
  Future<FinanceSummary> summary({String? month}) async =>
      FinanceSummary.fromJson(JsonReader.of(
          await api.request('GET', '$base/summary${_q({'month': month})}')));

  @override
  Future<List<FinanceAccount>> accounts() async => _list(
      await api.request('GET', '$base/accounts'), FinanceAccount.fromJson);

  @override
  Future<FinanceAccount> createAccount(
          {required String name,
          required String kind,
          String? last4,
          String openingBalance = '0'}) async =>
      FinanceAccount.fromJson(
          JsonReader.of(await api.request('POST', '$base/accounts', body: {
        'name': name,
        'kind': kind,
        if (last4 != null && last4.isNotEmpty) 'last4': last4,
        'opening_balance': openingBalance,
      })));

  @override
  Future<void> deleteAccount(String id) async {
    await api.request('DELETE', '$base/accounts/${Uri.encodeComponent(id)}');
  }

  @override
  Future<TransactionPage> transactions(
          {String? query, required int offset, int limit = 25}) async =>
      TransactionPage.fromJson(JsonReader.of(await api.request(
          'GET',
          '$base/transactions${_q({
                'q': query,
                'offset': '$offset',
                'limit': '$limit'
              })}')));

  @override
  Future<void> createTransaction(
      {required String accountId,
      required String postedOn,
      required String amount,
      required String description}) async {
    await api.request('POST', '$base/transactions', body: {
      'account_id': accountId,
      'posted_on': postedOn,
      'amount': amount,
      'description': description,
    });
  }

  @override
  Future<void> deleteTransaction(String id) async {
    await api.request(
        'DELETE', '$base/transactions/${Uri.encodeComponent(id)}');
  }

  @override
  Future<List<FinanceItem>> items(String kind) async {
    final body = await api.request('GET', '$base/$kind');
    return switch (kind) {
      'bills' => _list(body, FinanceItem.bill),
      'debts' => _list(body, FinanceItem.debt),
      _ => _list(body, FinanceItem.goal),
    };
  }

  @override
  Future<void> createBill(
      {required String name,
      required String amount,
      required String dueOn}) async {
    await api.request('POST', '$base/bills', body: {
      'name': name,
      'amount': amount,
      'cadence': 'monthly',
      'next_due_on': dueOn,
    });
  }

  @override
  Future<void> deleteItem(String kind, String id) async {
    await api.request('DELETE', '$base/$kind/${Uri.encodeComponent(id)}');
  }
}
