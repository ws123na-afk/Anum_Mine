import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import 'calls_models.dart';

/// Phone calls API (docs/calls.md#api). The server checks call:read,
/// call:log and call:manage on every route.
abstract interface class CallsRepository {
  Future<CallsSetup> setup();
  Future<CallsPage<CallContact>> contacts(
      {String query, bool favourites, int offset});
  Future<CallContact> createContact(
      {required String name,
      required String phone,
      String? company,
      String source});
  Future<CallContact> updateContact(String id, Map<String, Object?> changes);
  Future<void> deleteContact(String id);
  Future<CallsPage<CallEntry>> calls(
      {String query, String outcome, String? contactId, int offset});
  Future<CallEntry> call(String id);
  Future<CallEntry> logCall({
    String? contactId,
    String? phone,
    required String direction,
    required String outcome,
    int? durationSeconds,
    DateTime? startedAt,
    String? notes,
  });
  Future<CallEntry> updateCall(String id, Map<String, Object?> changes);
  Future<void> deleteCall(String id);
  Future<CallEntry> summarize(String id);
  Future<CallEntry> saveFollowUp(String id, String text, String? dueDate);
  Future<CallEntry> followUpTask(String id);

  /// The voice provider as an owner sees it (call:manage). The provider is
  /// connected and changed on the web; the phone shows its status.
  Future<ProviderStatus> provider();

  /// AI call requests, newest first, with their approval state.
  Future<CallsPage<AiCallRequest>> aiCalls({int offset});
  Future<AiCallRequest> aiCall(String id);

  /// Asks for an AI call. Nothing is dialled: the request waits for a
  /// workspace owner's approval of the exact number, purpose and script.
  Future<AiCallRequest> requestAiCall({
    required String contactId,
    required String purpose,
    required String script,
    required String language,
    required int maxSeconds,
  });

  /// Cancels a request still waiting for approval (its approval is withdrawn).
  Future<AiCallRequest> cancelAiCall(String id);
}

class ApiCallsRepository implements CallsRepository {
  const ApiCallsRepository(this.api);
  final AnumApiClient api;

  static const _base = '/api/v1/calls';
  String _one(String kind, String id, [String rest = '']) =>
      '$_base/$kind/${Uri.encodeComponent(id)}$rest';
  CallEntry _call(JsonMap body) => CallEntry.fromJson(JsonReader(body));
  CallContact _contact(JsonMap body) => CallContact.fromJson(JsonReader(body));

  String _path(String path, Map<String, String> params) =>
      Uri(path: path, queryParameters: params).toString();

  @override
  Future<CallsSetup> setup() async =>
      CallsSetup.fromJson(JsonReader(await api.request('GET', '$_base/setup')));

  @override
  Future<CallsPage<CallContact>> contacts(
      {String query = '', bool favourites = false, int offset = 0}) async {
    final j = JsonReader(await api.request(
        'GET',
        _path('$_base/contacts', {
          'limit': '$callsPageSize',
          'offset': '$offset',
          if (query.trim().isNotEmpty) 'q': query.trim(),
          if (favourites) 'favourites': 'true',
        })));
    return CallsPage(
        j.optObjects('items', CallContact.fromJson), j.optInt('total') ?? 0);
  }

  @override
  Future<CallContact> createContact(
          {required String name,
          required String phone,
          String? company,
          String source = 'manual'}) async =>
      _contact(await api.request('POST', '$_base/contacts', body: {
        'name': name,
        'phone': phone,
        if (company != null && company.isNotEmpty) 'company': company,
        'source': source,
      }));

  @override
  Future<CallContact> updateContact(
          String id, Map<String, Object?> changes) async =>
      _contact(await api.request('PATCH', _one('contacts', id), body: changes));

  @override
  Future<void> deleteContact(String id) async {
    await api.request('DELETE', _one('contacts', id));
  }

  @override
  Future<CallsPage<CallEntry>> calls(
      {String query = '',
      String outcome = '',
      String? contactId,
      int offset = 0}) async {
    final j = JsonReader(await api.request(
        'GET',
        _path('$_base/log', {
          'limit': '$callsPageSize',
          'offset': '$offset',
          if (query.trim().isNotEmpty) 'q': query.trim(),
          if (outcome.isNotEmpty) 'outcome': outcome,
          if (contactId != null) 'contact_id': contactId,
        })));
    return CallsPage(
        j.optObjects('items', CallEntry.fromJson), j.optInt('total') ?? 0);
  }

  @override
  Future<CallEntry> call(String id) async =>
      _call(await api.request('GET', _one('log', id)));

  @override
  Future<CallEntry> logCall({
    String? contactId,
    String? phone,
    required String direction,
    required String outcome,
    int? durationSeconds,
    DateTime? startedAt,
    String? notes,
  }) async =>
      _call(await api.request('POST', '$_base/log', body: {
        'kind': 'phone',
        if (contactId != null) 'contact_id': contactId,
        if (phone != null && phone.isNotEmpty) 'phone': phone,
        'direction': direction,
        'outcome': outcome,
        if (durationSeconds != null) 'duration_seconds': durationSeconds,
        if (startedAt != null)
          'started_at': startedAt.toUtc().toIso8601String(),
        if (notes != null && notes.trim().isNotEmpty) 'notes': notes.trim(),
      }));

  @override
  Future<CallEntry> updateCall(String id, Map<String, Object?> changes) async =>
      _call(await api.request('PATCH', _one('log', id), body: changes));

  @override
  Future<void> deleteCall(String id) async {
    await api.request('DELETE', _one('log', id));
  }

  @override
  Future<CallEntry> summarize(String id) async =>
      _call(await api.request('POST', _one('log', id, '/summarize')));

  @override
  Future<CallEntry> saveFollowUp(
          String id, String text, String? dueDate) async =>
      _call(await api.request('PUT', _one('log', id, '/follow-up'),
          body: {'text': text, if (dueDate != null) 'due_date': dueDate}));

  @override
  Future<CallEntry> followUpTask(String id) async =>
      _call(await api.request('POST', _one('log', id, '/follow-up/task')));

  AiCallRequest _ai(JsonMap body) => AiCallRequest.fromJson(JsonReader(body));

  @override
  Future<ProviderStatus> provider() async => ProviderStatus.fromJson(
      JsonReader(await api.request('GET', '$_base/provider')));

  @override
  Future<CallsPage<AiCallRequest>> aiCalls({int offset = 0}) async {
    final j = JsonReader(await api.request(
        'GET',
        _path('$_base/ai-calls',
            {'limit': '$callsPageSize', 'offset': '$offset'})));
    return CallsPage(
        j.optObjects('items', AiCallRequest.fromJson), j.optInt('total') ?? 0);
  }

  @override
  Future<AiCallRequest> aiCall(String id) async =>
      _ai(await api.request('GET', _one('ai-calls', id)));

  @override
  Future<AiCallRequest> requestAiCall({
    required String contactId,
    required String purpose,
    required String script,
    required String language,
    required int maxSeconds,
  }) async =>
      _ai(await api.request('POST', '$_base/ai-calls', body: {
        'contact_id': contactId,
        'purpose': purpose.trim(),
        'script': script.trim(),
        'language': language,
        'max_seconds': maxSeconds,
      }));

  @override
  Future<AiCallRequest> cancelAiCall(String id) async =>
      _ai(await api.request('POST', _one('ai-calls', id, '/cancel')));
}

/// Opens the phone's own dialer with a number (a `tel:` link). The phone
/// places the call: it is free and nothing goes through ANUM.
typedef PhoneDialer = Future<bool> Function(Uri tel);

Future<bool> openPhoneDialer(Uri tel) =>
    launchUrl(tel, mode: LaunchMode.externalApplication);

/// Lets the person pick one contact from the phone's address book with the
/// system picker. Only the picked contact is returned; nothing else is read.
typedef ContactPicker = Future<DeviceContact?> Function();

const _contactsChannel = MethodChannel('anum/contact_picker');

Future<DeviceContact?> pickDeviceContact() async {
  final picked =
      await _contactsChannel.invokeMapMethod<String, Object?>('pickContact');
  final name = picked?['name'], phone = picked?['phone'];
  if (name is! String || phone is! String || phone.trim().isEmpty) return null;
  return DeviceContact(
      name: name.trim().isEmpty ? phone : name.trim(), phone: phone);
}
