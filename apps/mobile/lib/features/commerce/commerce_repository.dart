import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'commerce_models.dart';

/// Commerce over the live API (docs/commerce.md). Read-only on the phone:
/// price and stock changes are proposed on the web and approved in Approvals.
abstract interface class CommerceRepository {
  Future<CommerceDashboard> dashboard(String from, String to);
  Future<SalesSummary> summary(String period, String lang);
  Future<OrderPage> orders({String? status, required int offset, int limit});
  Future<CommerceOrder> order(String id);
}

class ApiCommerceRepository implements CommerceRepository {
  ApiCommerceRepository(this.api);
  final AnumApiClient api;

  String _q(Map<String, String?> values) {
    final kept = {
      for (final e in values.entries)
        if (e.value != null && e.value!.isNotEmpty) e.key: e.value!
    };
    return kept.isEmpty ? '' : '?${Uri(queryParameters: kept).query}';
  }

  @override
  Future<CommerceDashboard> dashboard(String from, String to) async =>
      CommerceDashboard.fromJson(JsonReader.of(await api.request(
          'GET', '/api/v1/commerce/dashboard${_q({'from': from, 'to': to})}')));

  @override
  Future<SalesSummary> summary(String period, String lang) async =>
      SalesSummary.fromJson(JsonReader.of(await api.request('GET',
          '/api/v1/commerce/summary${_q({'period': period, 'lang': lang})}')));

  @override
  Future<OrderPage> orders(
          {String? status, required int offset, int limit = 20}) async =>
      OrderPage.fromJson(JsonReader.of(await api.request(
          'GET',
          '/api/v1/commerce/orders${_q({
                'status': status,
                'offset': '$offset',
                'limit': '$limit'
              })}')));

  @override
  Future<CommerceOrder> order(String id) async =>
      CommerceOrder.fromJson(JsonReader.of(await api.request(
          'GET', '/api/v1/commerce/orders/${Uri.encodeComponent(id)}')));
}
