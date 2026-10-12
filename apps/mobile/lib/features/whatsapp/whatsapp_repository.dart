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
}

class ApiWhatsAppRepository implements WhatsAppRepository {
  const ApiWhatsAppRepository(this.api);
  final AnumApiClient api;

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
  Future<String?> currentRole() async {
    final value = JsonReader(
        await api.request('GET', '/api/v1/workspace-memberships/current'));
    return value['active'] == false ? null : value.optString('role');
  }
}
