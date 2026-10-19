import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'business_models.dart';
import 'support_models.dart';

/// Business support tickets over the live API (docs/business.md#support-tickets).
/// The phone opens, works and resolves tickets and drafts replies; a reply is
/// only delivered after an owner approves it in Approvals.
abstract interface class SupportRepository {
  Future<SupportSetup> setup();
  Future<SupportSummary> summary();
  Future<List<SupportAssignee>> assignees();
  Future<BusinessPage<SupportTicket>> tickets(
    TicketFilter filter, {
    required int offset,
    int limit,
  });
  Future<TicketDetail> create(Map<String, Object?> fields);
  Future<TicketDetail> ticket(String id);
  Future<TicketDetail> update(String id, Map<String, Object?> fields);
  Future<void> delete(String id);
  Future<TicketDetail> changeStatus(
    String id,
    String to, {
    String? resolutionCode,
    String? resolutionSummary,
    String? note,
  });
  Future<TicketDetail> escalate(String id, bool escalated, String reason);
  Future<TicketDetail> addNote(String id, String text, bool responded);
  Future<TicketDetail> addReply(String id, String body);
  Future<TicketDetail> editReply(String id, String replyId, String body);
  Future<TicketDetail> discardReply(String id, String replyId);

  /// Hands the reply to the inbox's approval path; returns the fresh ticket
  /// and the API's sentence on what happened.
  Future<(TicketDetail, String)> requestApproval(String id, String replyId);
}

class ApiSupportRepository implements SupportRepository {
  ApiSupportRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/business/support';

  String _id(String value) => Uri.encodeComponent(value);

  Future<JsonReader> _call(String method, String path,
          {Map<String, Object?>? body}) async =>
      JsonReader.of(await api.request(method, '$_base$path', body: body));

  Future<TicketDetail> _detail(String method, String path,
          {Map<String, Object?>? body}) async =>
      TicketDetail.fromJson(await _call(method, path, body: body));

  @override
  Future<SupportSetup> setup() async =>
      SupportSetup.fromJson(await _call('GET', '/setup'));

  @override
  Future<SupportSummary> summary() async =>
      SupportSummary.fromJson(await _call('GET', '/summary'));

  @override
  Future<List<SupportAssignee>> assignees() async {
    // The route returns a bare list; the transport wraps it as `data`.
    final raw = await api.request('GET', '$_base/assignees');
    final items = raw['data'];
    return [
      for (final item in items is List ? items : const <Object?>[])
        SupportAssignee.fromJson(JsonReader.of(item, 'assignee')),
    ];
  }

  @override
  Future<BusinessPage<SupportTicket>> tickets(
    TicketFilter filter, {
    required int offset,
    int limit = 20,
  }) async {
    final query = Uri(queryParameters: {
      ...filter.toQuery(),
      'offset': '$offset',
      'limit': '$limit',
    }).query;
    return BusinessPage.fromJson(
      await _call('GET', '/tickets?$query'),
      SupportTicket.fromJson,
    );
  }

  @override
  Future<TicketDetail> create(Map<String, Object?> fields) =>
      _detail('POST', '/tickets', body: fields);

  @override
  Future<TicketDetail> ticket(String id) =>
      _detail('GET', '/tickets/${_id(id)}');

  @override
  Future<TicketDetail> update(String id, Map<String, Object?> fields) =>
      _detail('PATCH', '/tickets/${_id(id)}', body: fields);

  @override
  Future<void> delete(String id) =>
      api.request('DELETE', '$_base/tickets/${_id(id)}');

  @override
  Future<TicketDetail> changeStatus(
    String id,
    String to, {
    String? resolutionCode,
    String? resolutionSummary,
    String? note,
  }) =>
      _detail('POST', '/tickets/${_id(id)}/status', body: {
        'to': to,
        if (resolutionCode != null) 'resolution_code': resolutionCode,
        if (resolutionSummary != null) 'resolution_summary': resolutionSummary,
        if (note != null && note.isNotEmpty) 'note': note,
      });

  @override
  Future<TicketDetail> escalate(String id, bool escalated, String reason) =>
      _detail('POST', '/tickets/${_id(id)}/escalation',
          body: {'escalated': escalated, 'reason': reason});

  @override
  Future<TicketDetail> addNote(String id, String text, bool responded) =>
      _detail('POST', '/tickets/${_id(id)}/notes',
          body: {'text': text, 'responded': responded});

  @override
  Future<TicketDetail> addReply(String id, String body) =>
      _detail('POST', '/tickets/${_id(id)}/replies', body: {'body': body});

  @override
  Future<TicketDetail> editReply(String id, String replyId, String body) =>
      _detail('PATCH', '/tickets/${_id(id)}/replies/${_id(replyId)}',
          body: {'body': body});

  @override
  Future<TicketDetail> discardReply(String id, String replyId) =>
      _detail('DELETE', '/tickets/${_id(id)}/replies/${_id(replyId)}');

  @override
  Future<(TicketDetail, String)> requestApproval(
      String id, String replyId) async {
    final r = await _call(
      'POST',
      '/tickets/${_id(id)}/replies/${_id(replyId)}/request-approval',
    );
    return (
      TicketDetail.fromJson(r.object('ticket')),
      r.optString('detail') ?? ''
    );
  }
}
