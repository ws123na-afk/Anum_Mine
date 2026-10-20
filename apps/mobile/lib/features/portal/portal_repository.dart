import 'dart:convert';
import 'dart:typed_data';

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import 'portal_models.dart';

/// The customer portal over the live API (`/api/v1/portal`,
/// docs/business-portal.md). Every call carries the link's token as a bearer
/// header and no workspace header; the token lives in memory only.
abstract interface class PortalRepository {
  Future<PortalMe> me();
  Future<List<PortalOfferSummary>> offers();
  Future<PortalOffer> offer(String id);
  Future<PortalOffer> decideOffer(String id,
      {required String decision,
      required String contentHash,
      required String signerName,
      String note});
  Future<List<PortalChangeSummary>> changeOrders();
  Future<PortalChange> changeOrder(String id);
  Future<PortalChange> decideChangeOrder(String id,
      {required String decision,
      required String contentHash,
      required String signerName,
      String note});
  Future<List<PortalInvoiceSummary>> invoices();
  Future<PortalInvoice> invoice(String id);
  Future<String> printHtml(String id, String lang);

  /// The PDF made on the server for their own invoice, as bytes.
  Future<Uint8List> pdf(String id);

  /// Tells the business a payment was made. A report changes no balance: the
  /// business checks its bank and confirms or rejects it.
  Future<PortalInvoice> reportPayment(
    String id, {
    required String idempotencyKey,
    required String payerName,
    required double amount,
    required String paidOn,
    required String method,
    String reference,
    String note,
  });
  Future<List<PortalTicketSummary>> tickets();
  Future<PortalTicket> ticket(String id);
  Future<PortalTicket> openTicket({
    required String title,
    required String description,
    required String severity,
    String? projectId,
  });

  /// Forget the token. Nothing can be called afterwards.
  void close();
}

class ApiPortalRepository implements PortalRepository {
  ApiPortalRepository(this.api, String token) : _token = token;
  final AnumApiClient api;
  String? _token;
  static const _base = '/api/v1/portal';
  String _id(String value) => Uri.encodeComponent(value);

  Future<JsonMap> _call(String method, String path,
      [Map<String, Object?>? body]) {
    final token = _token;
    if (token == null) {
      throw const ApiException(
          401, 'You left the portal. Open your link again.');
    }
    return api.request(method, '$_base$path',
        body: body,
        authenticated: false,
        headers: {'authorization': 'Bearer $token'});
  }

  List<T> _list<T>(JsonMap body, T Function(JsonReader) read) {
    final items = body['data'];
    if (items is! List) throw JsonShapeException('data', 'a list', 'nothing');
    return [
      for (var i = 0; i < items.length; i++)
        read(JsonReader.of(items[i], 'data[$i]')),
    ];
  }

  @override
  void close() => _token = null;

  @override
  Future<PortalMe> me() async =>
      PortalMe.fromJson(JsonReader.of(await _call('GET', '/me')));

  @override
  Future<List<PortalOfferSummary>> offers() async =>
      _list(await _call('GET', '/offers'), PortalOfferSummary.fromJson);

  @override
  Future<PortalOffer> offer(String id) async => PortalOffer.fromJson(
      JsonReader.of(await _call('GET', '/offers/${_id(id)}')));

  Map<String, Object?> _decision(
          String decision, String hash, String name, String note) =>
      {
        'decision': decision,
        'content_hash': hash,
        'signer_name': name,
        if (note.isNotEmpty) 'note': note,
      };

  @override
  Future<PortalOffer> decideOffer(String id,
          {required String decision,
          required String contentHash,
          required String signerName,
          String note = ''}) async =>
      PortalOffer.fromJson(JsonReader.of(await _call(
          'POST',
          '/offers/${_id(id)}/decision',
          _decision(decision, contentHash, signerName, note))));

  @override
  Future<List<PortalChangeSummary>> changeOrders() async =>
      _list(await _call('GET', '/change-orders'), PortalChangeSummary.fromJson);

  @override
  Future<PortalChange> changeOrder(String id) async => PortalChange.fromJson(
      JsonReader.of(await _call('GET', '/change-orders/${_id(id)}')));

  @override
  Future<PortalChange> decideChangeOrder(String id,
          {required String decision,
          required String contentHash,
          required String signerName,
          String note = ''}) async =>
      PortalChange.fromJson(JsonReader.of(await _call(
          'POST',
          '/change-orders/${_id(id)}/decision',
          _decision(decision, contentHash, signerName, note))));

  @override
  Future<List<PortalInvoiceSummary>> invoices() async =>
      _list(await _call('GET', '/invoices'), PortalInvoiceSummary.fromJson);

  @override
  Future<PortalInvoice> invoice(String id) async => PortalInvoice.fromJson(
      JsonReader.of(await _call('GET', '/invoices/${_id(id)}')));

  @override
  Future<String> printHtml(String id, String lang) async {
    final body = await _call('GET',
        '/invoices/${_id(id)}/print?lang=${Uri.encodeQueryComponent(lang)}');
    final html = body['html'];
    if (html is! String || html.isEmpty) {
      throw const ApiException(
          0, 'The printable copy came back empty. Try again.');
    }
    return html;
  }

  @override
  Future<Uint8List> pdf(String id) async {
    final body = await _call('GET', '/invoices/${_id(id)}/pdf');
    final encoded = body['pdf_base64'];
    if (encoded is! String || encoded.isEmpty) {
      throw const ApiException(0, 'The PDF came back empty. Try again.');
    }
    return base64Decode(encoded);
  }

  @override
  Future<PortalInvoice> reportPayment(
    String id, {
    required String idempotencyKey,
    required String payerName,
    required double amount,
    required String paidOn,
    required String method,
    String reference = '',
    String note = '',
  }) async =>
      PortalInvoice.fromJson(JsonReader.of(
          await _call('POST', '/invoices/${_id(id)}/payment-notices', {
        'idempotency_key': idempotencyKey,
        'payer_name': payerName,
        'amount': amount,
        'paid_on': paidOn,
        'method': method,
        if (reference.isNotEmpty) 'reference': reference,
        if (note.isNotEmpty) 'note': note,
      })));

  @override
  Future<List<PortalTicketSummary>> tickets() async =>
      _list(await _call('GET', '/tickets'), PortalTicketSummary.fromJson);

  @override
  Future<PortalTicket> ticket(String id) async => PortalTicket.fromJson(
      JsonReader.of(await _call('GET', '/tickets/${_id(id)}')));

  @override
  Future<PortalTicket> openTicket({
    required String title,
    required String description,
    required String severity,
    String? projectId,
  }) async =>
      PortalTicket.fromJson(JsonReader.of(await _call('POST', '/tickets', {
        'title': title,
        'description': description,
        'severity': severity,
        if (projectId != null) 'project_id': projectId,
      })));
}
