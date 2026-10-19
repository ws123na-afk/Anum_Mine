import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/json_read.dart';
import 'business_models.dart';
import 'invoice_models.dart';

/// Invoices, the billing profile and portal links over the live API
/// (docs/business.md#invoices, docs/business-portal.md#giving-access). Kept
/// apart from `BusinessRepository` like the project money and support routes.
abstract interface class InvoicesRepository {
  Future<InvoiceSummary> summary();
  Future<BusinessPage<InvoiceListItem>> list(
    InvoiceFilter filter, {
    required int offset,
    int limit,
  });
  Future<Invoice> get(String id);
  Future<Billable> billable({String? accountId, String? projectId});
  Future<Invoice> create(Map<String, Object?> body);
  Future<Invoice> update(String id, Map<String, Object?> body);
  Future<void> delete(String id);
  Future<Invoice> requestApproval(String id);
  Future<Invoice> cancelRequest(String id);
  Future<Invoice> voidInvoice(String id, String reason);
  Future<Invoice> recordPayment(String id, Map<String, Object?> body);
  Future<Invoice> removePayment(String id, String paymentId);

  /// The printable page (`GET /invoices/{id}/print`), fetched with the
  /// session's own header: a link with a token in it is never built.
  Future<String> printHtml(String id, String lang);

  Future<BillingProfile> billingProfile();
  Future<BillingProfile> saveBillingProfile(BillingProfile profile);

  Future<BusinessPage<ClientAccount>> accounts({
    String? query,
    required int offset,
    int limit,
  });
  Future<ClientDetail> account(String id);
  Future<List<ProjectChoice>> projects();
  Future<List<PortalAccess>> portalAccess(String accountId);
  Future<PortalAccessCreated> createPortalAccess({
    required String accountId,
    required String contactId,
    required int expiresInDays,
    required bool canDecide,
  });
  Future<PortalAccess> revokePortalAccess(String id);
}

class ApiInvoicesRepository implements InvoicesRepository {
  ApiInvoicesRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/business';
  String _id(String value) => Uri.encodeComponent(value);

  String _q(Map<String, String?> values) {
    final kept = {
      for (final e in values.entries)
        if (e.value != null && e.value!.isNotEmpty) e.key: e.value!,
    };
    return kept.isEmpty ? '' : '?${Uri(queryParameters: kept).query}';
  }

  Future<JsonMap> _call(String method, String path,
          [Map<String, Object?>? body]) =>
      api.request(method, '$_base$path', body: body);

  Future<Invoice> _invoice(String method, String path,
          [Map<String, Object?>? body]) async =>
      Invoice.fromJson(JsonReader.of(await _call(method, path, body)));

  @override
  Future<InvoiceSummary> summary() async => InvoiceSummary.fromJson(
      JsonReader.of(await _call('GET', '/invoices/summary')));

  @override
  Future<BusinessPage<InvoiceListItem>> list(
    InvoiceFilter filter, {
    required int offset,
    int limit = 20,
  }) async =>
      BusinessPage.fromJson(
        JsonReader.of(await _call(
            'GET',
            '/invoices${_q({
                  ...filter.toQuery(),
                  'offset': '$offset',
                  'limit': '$limit',
                })}')),
        InvoiceListItem.fromJson,
      );

  @override
  Future<Invoice> get(String id) => _invoice('GET', '/invoices/${_id(id)}');

  @override
  Future<Billable> billable({String? accountId, String? projectId}) async =>
      Billable.fromJson(JsonReader.of(await _call(
          'GET',
          '/invoices/billable${_q({
                'account_id': accountId,
                'project_id': projectId
              })}')));

  @override
  Future<Invoice> create(Map<String, Object?> body) =>
      _invoice('POST', '/invoices', body);

  @override
  Future<Invoice> update(String id, Map<String, Object?> body) =>
      _invoice('PATCH', '/invoices/${_id(id)}', body);

  @override
  Future<void> delete(String id) async {
    await _call('DELETE', '/invoices/${_id(id)}');
  }

  @override
  Future<Invoice> requestApproval(String id) =>
      _invoice('POST', '/invoices/${_id(id)}/request-approval');

  @override
  Future<Invoice> cancelRequest(String id) =>
      _invoice('POST', '/invoices/${_id(id)}/cancel-request');

  @override
  Future<Invoice> voidInvoice(String id, String reason) =>
      _invoice('POST', '/invoices/${_id(id)}/void', {'reason': reason});

  @override
  Future<Invoice> recordPayment(String id, Map<String, Object?> body) =>
      _invoice('POST', '/invoices/${_id(id)}/payments', body);

  @override
  Future<Invoice> removePayment(String id, String paymentId) =>
      _invoice('DELETE', '/invoices/${_id(id)}/payments/${_id(paymentId)}');

  @override
  Future<String> printHtml(String id, String lang) async {
    final body = await _call(
        'GET', '/invoices/${_id(id)}/print${_q({'lang': lang})}');
    final html = body['html'];
    if (html is! String || html.isEmpty) {
      throw const ApiException(
          0, 'The printable copy came back empty. Try again.');
    }
    return html;
  }

  @override
  Future<BillingProfile> billingProfile() async => BillingProfile.fromJson(
      JsonReader.of(await _call('GET', '/billing-profile')));

  @override
  Future<BillingProfile> saveBillingProfile(BillingProfile profile) async =>
      BillingProfile.fromJson(JsonReader.of(
          await _call('PUT', '/billing-profile', profile.toJson())));

  @override
  Future<BusinessPage<ClientAccount>> accounts({
    String? query,
    required int offset,
    int limit = 20,
  }) async =>
      BusinessPage.fromJson(
        JsonReader.of(await _call(
            'GET',
            '/accounts${_q({
                  'q': query,
                  'offset': '$offset',
                  'limit': '$limit'
                })}')),
        ClientAccount.fromJson,
      );

  @override
  Future<ClientDetail> account(String id) async => ClientDetail.fromJson(
      JsonReader.of(await _call('GET', '/accounts/${_id(id)}')));

  @override
  Future<List<ProjectChoice>> projects() async {
    final page = BusinessPage.fromJson(
      JsonReader.of(await _call('GET', '/projects?limit=100')),
      ProjectChoice.fromJson,
    );
    return [
      for (final item in page.items)
        if (item.status != 'closed') item,
    ];
  }

  @override
  Future<List<PortalAccess>> portalAccess(String accountId) async {
    final body =
        await _call('GET', '/portal/access${_q({'account_id': accountId})}');
    final items = body['data'];
    if (items is! List) {
      throw JsonShapeException('data', 'a list', 'something else');
    }
    return [
      for (var i = 0; i < items.length; i++)
        PortalAccess.fromJson(JsonReader.of(items[i], 'data[$i]')),
    ];
  }

  @override
  Future<PortalAccessCreated> createPortalAccess({
    required String accountId,
    required String contactId,
    required int expiresInDays,
    required bool canDecide,
  }) async =>
      PortalAccessCreated.fromJson(JsonReader.of(await _call(
          'POST', '/portal/access', {
        'account_id': accountId,
        'contact_id': contactId,
        'expires_in_days': expiresInDays,
        'can_decide': canDecide,
      })));

  @override
  Future<PortalAccess> revokePortalAccess(String id) async =>
      PortalAccess.fromJson(JsonReader.of(
          await _call('POST', '/portal/access/${_id(id)}/revoke')));
}
