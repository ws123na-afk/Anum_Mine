import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../records_kit/records_kit.dart';
import 'finance_core_models.dart';
import 'finance_personal_models.dart';

/// The routes behind the phone's transaction register, a transaction's
/// detail and the salary history (docs/finance.md#transaction-detail,
/// docs/finance.md#salary). Every row is the caller's own; the server checks
/// `finance:read` and `finance:manage` on each call.
class FinanceCoreRepository {
  const FinanceCoreRepository(this.api);
  final AnumApiClient api;
  static const base = '/api/v1/finance';
  static const pageSize = 25;

  String _id(String value) => Uri.encodeComponent(value);

  /// Transactions with the filters, paged on the server.
  Future<RegisterPage> register(RegisterFilters filters,
          {int offset = 0, int limit = pageSize}) async =>
      RegisterPage.fromJson(JsonReader.of(await api.request(
          'GET',
          '$base/register${recordsQuery({
                ...filters.toQuery(),
                'limit': limit,
                'offset': offset
              })}')));

  Future<RegisterDetail> detail(String id) async => RegisterDetail.fromJson(
      JsonReader.of(await api.request('GET', '$base/register/${_id(id)}')));

  /// The owner's note: never changes the amount or the category.
  Future<void> saveNote(String id, String? note) =>
      api.request('PATCH', '$base/transactions/${_id(id)}',
          body: {'notes': (note ?? '').trim().isEmpty ? null : note!.trim()});

  /// The owner's category: a rule never changes it afterwards. A row an SMS or
  /// Apple Pay recorded answers 409 (it is corrected under iPhone SMS, Review).
  Future<void> saveCategory(String id, String category) =>
      api.request('PATCH', '$base/transactions/${_id(id)}',
          body: {'category': category});

  /// Expected against received, month by month, newest first.
  Future<(List<SalaryMonth>, int, String?)> salaryMonths(
      {int offset = 0, int limit = 12}) async {
    final r = JsonReader.of(await api.request(
        'GET',
        '$base/salary/months${recordsQuery({
              'limit': limit,
              'offset': offset
            })}'));
    return (
      r.optObjects('months', SalaryMonth.fromJson),
      r.optInt('total') ?? 0,
      r.optString('currency'),
    );
  }
}
