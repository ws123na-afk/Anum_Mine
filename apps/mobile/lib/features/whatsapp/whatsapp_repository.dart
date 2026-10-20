import 'dart:typed_data';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../workspace/workspace_models.dart';
import 'whatsapp_models.dart';

/// The WhatsApp inbox over the API (docs/whatsapp.md). The server checks
/// every permission: owners and members read and ask to send; only owners
/// approve a send (with the payload hash the app displayed).
abstract interface class WhatsAppRepository {
  Future<WaAccount> account();
  Future<List<WaConversation>> conversations(
      {int limit = 30, String? before, String? query, WaFilter filter});
  Future<WaThread> thread(String id, {int limit = 30, String? before});
  Future<WaConversation> markRead(String id);
  Future<WaSuggestion> draft(String id);
  Future<WaSendResult> sendText(String id, String text);

  /// `POST /api/v1/approvals/{id}/approve {"payload_hash": ...}`; returns the
  /// run's result sentence when the API sends one.
  Future<String?> approve(WorkspaceApproval approval);
  Future<void> reject(WorkspaceApproval approval);

  /// The caller's role in this workspace (approving needs `owner`).
  Future<String?> currentRole();

  /// `GET /api/v1/whatsapp/templates` (`?refresh=true` asks Meta again).
  Future<WaTemplateList> templates({bool refresh = false});

  /// `POST /conversations/{id}/send {"template": {name, language, parameters}}`.
  Future<WaSendResult> sendTemplate(
      String id, WaTemplate template, List<String> parameters);

  /// `PUT /api/v1/whatsapp/account` (owners). Secrets in [update] are sent
  /// once and never read back.
  Future<WaAccount> saveAccount(WaAccountUpdate update);

  /// `POST /api/v1/whatsapp/account/check` (owners).
  Future<WaAccount> checkAccount();

  /// `POST /api/v1/whatsapp/account/test` (owners): the number the test
  /// message went to.
  Future<String> sendTest();

  /// `DELETE /api/v1/whatsapp/account` (owners).
  Future<void> disconnect();

  /// `GET /api/v1/whatsapp/messages/{id}/media`: the attachment's bytes.
  Future<Uint8List> mediaBytes(String messageId);

  /// Hands [bytes] to the platform to save; false when the person cancelled.
  Future<bool> saveMedia(String fileName, Uint8List bytes);
}

/// Bytes of an API download (see `HttpMailFileTransfer.download`).
typedef WaDownload = Future<Uint8List> Function(String path);

/// Saves bytes with the platform's save dialog.
typedef WaSave = Future<bool> Function(String fileName, Uint8List bytes);

class ApiWhatsAppRepository implements WhatsAppRepository {
  const ApiWhatsAppRepository(this.api, {this.download, this.save});
  final AnumApiClient api;

  /// The bytes-capable call for media (the JSON client cannot carry them);
  /// null in builds without file access.
  final WaDownload? download;
  final WaSave? save;

  static const _base = '/api/v1/whatsapp';
  static String _id(String value) => Uri.encodeComponent(value);

  @override
  Future<WaAccount> account() async => WaAccount.fromJson(
      JsonReader(await api.request('GET', '$_base/account')));

  @override
  Future<List<WaConversation>> conversations(
      {int limit = 30,
      String? before,
      String? query,
      WaFilter filter = WaFilter.all}) async {
    final path = Uri(path: '$_base/conversations', queryParameters: {
      'limit': '$limit',
      'filter': filter.api,
      if (before != null) 'before': before,
      if (query != null && query.trim().isNotEmpty) 'q': query.trim(),
    });
    // The API answers with a JSON array; the transport wraps it as `data`.
    return JsonReader(await api.request('GET', path.toString()))
        .objects('data', WaConversation.fromJson);
  }

  @override
  Future<WaThread> thread(String id, {int limit = 30, String? before}) async {
    final path = Uri(path: '$_base/conversations/${_id(id)}', queryParameters: {
      'limit': '$limit',
      if (before != null) 'before': before,
    });
    return WaThread.fromJson(
        JsonReader(await api.request('GET', path.toString())));
  }

  @override
  Future<WaConversation> markRead(String id) async =>
      WaConversation.fromJson(JsonReader(
          await api.request('POST', '$_base/conversations/${_id(id)}/read')));

  @override
  Future<WaSuggestion> draft(String id) async =>
      WaSuggestion.fromJson(JsonReader(await api.request(
          'POST', '$_base/conversations/${_id(id)}/draft',
          body: const {})));

  @override
  Future<WaSendResult> sendText(String id, String text) async =>
      WaSendResult.fromJson(JsonReader(await api.request(
          'POST', '$_base/conversations/${_id(id)}/send',
          body: {'text': text})));

  @override
  Future<String?> approve(WorkspaceApproval approval) async {
    final value = JsonReader(await api.request(
        'POST', '/api/v1/approvals/${_id(approval.id)}/approve',
        body: decisionBody(approval, null)));
    return value.optObject('run')?.optString('result');
  }

  @override
  Future<void> reject(WorkspaceApproval approval) async {
    await api.request('POST', '/api/v1/approvals/${_id(approval.id)}/reject',
        body: decisionBody(approval, null));
  }

  @override
  Future<WaTemplateList> templates({bool refresh = false}) async =>
      WaTemplateList.fromJson(JsonReader(await api.request(
          'GET', '$_base/templates${refresh ? '?refresh=true' : ''}')));

  @override
  Future<WaSendResult> sendTemplate(
          String id, WaTemplate template, List<String> parameters) async =>
      WaSendResult.fromJson(JsonReader(await api
          .request('POST', '$_base/conversations/${_id(id)}/send', body: {
        'template': {
          'name': template.name,
          'language': template.language,
          'parameters': [for (final p in parameters) p.trim()],
        },
      })));

  @override
  Future<WaAccount> saveAccount(WaAccountUpdate update) async =>
      WaAccount.fromJson(JsonReader(
          await api.request('PUT', '$_base/account', body: update.body())));

  @override
  Future<WaAccount> checkAccount() async => WaAccount.fromJson(
      JsonReader(await api.request('POST', '$_base/account/check')));

  @override
  Future<String> sendTest() async => JsonReader(
          await api.request('POST', '$_base/account/test', body: const {}))
      .string('to');

  @override
  Future<void> disconnect() async {
    await api.request('DELETE', '$_base/account');
  }

  @override
  Future<Uint8List> mediaBytes(String messageId) {
    final fetch = download;
    if (fetch == null) {
      throw UnsupportedError('Files are not available in this build.');
    }
    return fetch('$_base/messages/${_id(messageId)}/media');
  }

  @override
  Future<bool> saveMedia(String fileName, Uint8List bytes) {
    final saver = save;
    if (saver == null) {
      throw UnsupportedError('Files are not available in this build.');
    }
    return saver(fileName, bytes);
  }

  @override
  Future<String?> currentRole() async {
    final value = JsonReader(
        await api.request('GET', '/api/v1/workspace-memberships/current'));
    return value['active'] == false ? null : value.optString('role');
  }
}
