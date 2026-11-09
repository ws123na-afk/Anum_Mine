import '../../data/api_client.dart';
import '../../data/api_models.dart';
import 'finance_models.dart';
import 'finance_plan_models.dart';
import 'finance_review_models.dart';
import 'finance_trading_models.dart';

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

  /// The transactions, newest first. With [type], only rows of that
  /// transaction type (`uncategorized` for the rows not counted yet).
  Future<TransactionPage> transactions(
      {String? query, required int offset, int limit = 25, String? type});

  /// How many rows no rule recognised: held, counted as neither income nor
  /// spending until the owner files them.
  Future<int> uncategorizedCount();

  /// Files a row as [type], under [category] when given (else the server
  /// files it under that type's usual category). The owner's own choice: a
  /// rule never changes it afterwards.
  Future<void> fileTransaction(String id,
      {required String type, String? category});
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
  // Stages 6 to 9: SIMULATED portfolios, requests waiting for the person's
  // approval with their code, the kill switch, and live use (off).
  Future<List<PortfolioSummary>> portfolios();
  Future<List<TradeIntent>> pendingIntents();
  Future<void> approveIntent(String id, String payloadHash);
  Future<void> rejectIntent(String id);
  Future<ExecutionState> executionState();
  Future<void> setKillSwitch(bool engaged);
  Future<LiveState> liveState();
  Future<void> reviewIncidents();
  // Stages 3 to 5: safe to spend, the Guardian rules, read-only connections.
  Future<SafeToSpend> safeToSpend();
  Future<GuardianView> guardian();
  Future<List<GuardianAlert>> guardianAlerts();

  /// Confirms a proposed change with the payload hash the person was shown.
  Future<void> confirmChange(String id, String payloadHash);
  Future<void> rejectChange(String id);
  Future<List<FinanceConnection>> connections();
  Future<void> syncConnection(String id);

  /// The newest statement import with its counts, or null when none.
  Future<ImportRecord?> lastImport();

  /// Connection health per account with the open reconciliation sessions.
  Future<ReconStatus> reconciliation();

  /// The suggestion queue (docs/finance.md#suggestions), one page.
  Future<SuggestionQueue> suggestions({required int offset, int limit = 25});

  /// Accepts exactly the suggestion shown, with its payload hash.
  Future<SuggestionDecision> acceptSuggestion(String key, String payloadHash);

  /// Dismisses a suggestion; it does not come back.
  Future<void> dismissSuggestion(String key);
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
          {String? query,
          required int offset,
          int limit = 25,
          String? type}) async =>
      // A type filter reads the register, which filters by type on the
      // server; its rows carry every transaction field.
      TransactionPage.fromJson(JsonReader.of(await api.request(
          'GET',
          '$base/${type == null ? 'transactions' : 'register'}${_q({
                'q': query,
                'type': type,
                'offset': '$offset',
                'limit': '$limit'
              })}')));

  @override
  Future<int> uncategorizedCount() async =>
      JsonReader.of(await api.request(
              'GET', '$base/register?type=uncategorized&limit=1'))
          .optInt('total') ??
      0;

  @override
  Future<void> fileTransaction(String id,
      {required String type, String? category}) async {
    await api.request('PATCH', '$base/transactions/${Uri.encodeComponent(id)}',
        body: {
          'transaction_type': type,
          if (category != null) 'category': category,
        });
  }

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

  @override
  Future<SafeToSpend> safeToSpend() async {
    final safe = SafeToSpend.fromJson(
        JsonReader.of(await api.request('GET', '$base/safe-to-spend')));
    // The forecast and the reserve (docs/finance.md#forecast) come with it; if
    // they cannot be read, Safe to spend still shows and says the forecast failed.
    try {
      final forecast =
          JsonReader.of(await api.request('GET', '$base/forecast'));
      final reserve = JsonReader.of(await api.request('GET', '$base/reserve'));
      return safe.withForecast(
          forecast.optObjects('currencies', CurrencyForecast.fromJson),
          reserve.optObjects('reserves', ReserveProgress.fromJson));
    } on ApiException catch (error) {
      if (error.statusCode == 401 || error.statusCode == 403) rethrow;
      return safe.withForecast(const [], const [], failed: true);
    } on JsonShapeException {
      return safe.withForecast(const [], const [], failed: true);
    }
  }

  @override
  Future<GuardianView> guardian() async => GuardianView.fromJson(
      JsonReader.of(await api.request('GET', '$base/guardian')));

  @override
  Future<List<GuardianAlert>> guardianAlerts() async =>
      JsonReader.of(await api.request('GET', '$base/guardian/alerts'))
          .optObjects('alerts', GuardianAlert.fromJson);

  @override
  Future<void> confirmChange(String id, String payloadHash) async {
    await api.request(
        'POST', '$base/guardian/changes/${Uri.encodeComponent(id)}/confirm',
        body: {'payload_hash': payloadHash});
  }

  @override
  Future<void> rejectChange(String id) async {
    await api.request(
        'POST', '$base/guardian/changes/${Uri.encodeComponent(id)}/reject');
  }

  @override
  Future<List<FinanceConnection>> connections() async => _list(
      await api.request('GET', '$base/connections'),
      FinanceConnection.fromJson);

  @override
  Future<void> syncConnection(String id) async {
    await api.request(
        'POST', '$base/connections/${Uri.encodeComponent(id)}/sync');
  }

  @override
  Future<ImportRecord?> lastImport() async =>
      JsonReader.of(await api.request('GET', '$base/imports?limit=1'))
          .optObjects('items', ImportRecord.fromJson)
          .firstOrNull;

  @override
  Future<ReconStatus> reconciliation() async {
    final found = await Future.wait([
      api.request('GET', '$base/reconciliations/health'),
      api.request('GET', '$base/reconciliations?state=open&limit=50'),
    ]);
    return ReconStatus(
      accounts:
          JsonReader.of(found[0]).optObjects('accounts', ReconHealth.fromJson),
      open: JsonReader.of(found[1]).optObjects('items', ReconSession.fromJson),
    );
  }

  @override
  Future<SuggestionQueue> suggestions(
          {required int offset, int limit = 25}) async =>
      SuggestionQueue.fromJson(JsonReader.of(await api.request(
          'GET', '$base/suggestions?limit=$limit&offset=$offset')));

  @override
  Future<SuggestionDecision> acceptSuggestion(
          String key, String payloadHash) async =>
      SuggestionDecision.fromJson(JsonReader.of(await api.request(
          'POST', '$base/suggestions/accept',
          body: {'key': key, 'payload_hash': payloadHash})));

  @override
  Future<void> dismissSuggestion(String key) async {
    await api.request('POST', '$base/suggestions/reject', body: {'key': key});
  }

  @override
  Future<List<PortfolioSummary>> portfolios() async => _list(
      await api.request('GET', '$base/paper/portfolios'),
      PortfolioSummary.fromJson);

  @override
  Future<List<TradeIntent>> pendingIntents() async =>
      JsonReader.of(await api.request('GET',
              '$base/execution/intents?status=pending_approval&limit=50'))
          .optObjects('items', TradeIntent.fromJson);

  @override
  Future<void> approveIntent(String id, String payloadHash) async {
    await api.request(
        'POST', '$base/execution/intents/${Uri.encodeComponent(id)}/approve',
        body: {'payload_hash': payloadHash});
  }

  @override
  Future<void> rejectIntent(String id) async {
    await api.request(
        'POST', '$base/execution/intents/${Uri.encodeComponent(id)}/reject',
        body: const <String, Object?>{});
  }

  @override
  Future<ExecutionState> executionState() async => ExecutionState.fromJson(
      JsonReader.of(await api.request('GET', '$base/execution/status')));

  @override
  Future<void> setKillSwitch(bool engaged) async {
    await api.request('PUT', '$base/execution/kill-switch',
        body: {'scope': 'person', 'engaged': engaged});
  }

  @override
  Future<LiveState> liveState() async {
    final found = await Future.wait([
      api.request('GET', '$base/execution/live'),
      api.request('GET', '$base/execution/wider'),
    ]);
    return LiveState.from(JsonReader.of(found[0]), JsonReader.of(found[1]));
  }

  @override
  Future<void> reviewIncidents() async {
    await api.request('POST', '$base/execution/wider/review',
        body: const <String, Object?>{});
  }
}
