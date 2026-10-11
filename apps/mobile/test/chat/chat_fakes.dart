import 'dart:async';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/data/sse.dart';
import 'package:anum_mobile/features/chat/chat_controller.dart';
import 'package:anum_mobile/features/chat/chat_repository.dart';

JsonMap turnJson(String id, String role, String content,
        {String status = 'complete', String conversationId = 'conv_1'}) =>
    {
      'id': id,
      'conversation_id': conversationId,
      'role': role,
      'content': content,
      'status': status,
      'created_at': '2026-10-11T10:00:00Z',
    };

/// The conversations API in memory, like `ConversationStore` on the server.
class FakeConversationsApi implements ApiTransport {
  FakeConversationsApi(
      {List<JsonMap>? conversations, Map<String, List<JsonMap>>? turns})
      : conversations = conversations ?? [],
        turns = turns ?? {};

  final List<JsonMap> conversations;
  final Map<String, List<JsonMap>> turns;
  final requests = <ApiRequest>[];

  /// Answer every list with this status (e.g. 403) instead of the data.
  int? listStatus;

  /// Answer deletes with this status (e.g. 500).
  int? deleteStatus;
  int _ids = 0;

  Iterable<ApiRequest> where(String method, [String? path]) => requests
      .where((r) => r.method == method && (path == null || r.uri.path == path));

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    const base = '/api/v1/conversations';
    final path = request.uri.path;
    if (path == base && request.method == 'GET') {
      final status = listStatus;
      if (status != null) {
        return ApiResponse(
            statusCode: status,
            body: {'detail': status == 403 ? 'Forbidden' : 'Unavailable'});
      }
      final sorted = [...conversations]..sort((a, b) =>
          (b['updated_at']! as String).compareTo(a['updated_at']! as String));
      return ApiResponse(statusCode: 200, body: {'data': sorted});
    }
    if (path == base && request.method == 'POST') {
      final created = conversation('conv_new_${++_ids}', '',
          updatedAt: '2026-10-11T12:00:0${_ids}Z');
      conversations.add(created);
      return ApiResponse(statusCode: 201, body: created);
    }
    final id = Uri.decodeComponent(path.substring(base.length + 1));
    final found = conversations.where((c) => c['id'] == id).toList();
    if (found.isEmpty) {
      return const ApiResponse(
          statusCode: 404, body: {'detail': 'Conversation not found'});
    }
    if (request.method == 'DELETE') {
      final status = deleteStatus;
      if (status != null) {
        return ApiResponse(
            statusCode: status, body: {'detail': 'Database down'});
      }
      conversations.removeWhere((c) => c['id'] == id);
      return const ApiResponse(statusCode: 204);
    }
    return ApiResponse(
        statusCode: 200, body: {...found.single, 'turns': turns[id] ?? []});
  }
}

JsonMap conversation(String id, String title,
        {String updatedAt = '2026-10-11T10:00:00Z'}) =>
    {
      'id': id,
      'tenant_id': 'tenant_test',
      'workspace_id': 'workspace_test',
      'user_id': 'user_test',
      'title': title,
      'created_at': '2026-10-01T09:00:00Z',
      'updated_at': updatedAt,
    };

/// A scripted `ask` stream: the test emits each event and ends it.
class ScriptedEvents implements EventStreamClient {
  final calls = <(String, JsonMap)>[];
  StreamController<SseEvent>? _current;
  int cancels = 0;

  /// The next ask fails at once with this error (e.g. a 402 ApiException).
  Object? refuseNext;

  @override
  Stream<SseEvent> post(String path, JsonMap body) {
    calls.add((path, body));
    final controller = StreamController<SseEvent>(onCancel: () => cancels++);
    _current = controller;
    final refusal = refuseNext;
    refuseNext = null;
    if (refusal != null) {
      controller.addError(refusal);
      unawaited(controller.close());
    }
    return controller.stream;
  }

  void emit(String event, Object? data) => _current!.add(SseEvent(event, data));

  void fail(Object error) => _current!.addError(error);

  Future<void> end() => _current!.close();
}

Future<MemorySessionStore> signedInSessions(
    {String workspaceId = 'workspace_test'}) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
    accessToken: 'example',
    tokenType: 'bearer',
    expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
    context: TenantContext(
        tenantId: 'tenant_test',
        workspaceId: workspaceId,
        userId: 'user_test',
        roles: const ['member']),
  ));
  return sessions;
}

Future<(ChatController, MemorySessionStore)> buildChat(
    FakeConversationsApi api, ScriptedEvents events) async {
  final sessions = await signedInSessions();
  final client = AnumApiClient(
      baseUri: Uri.parse('https://anum.test'),
      transport: api,
      sessions: sessions);
  return (ChatController(ChatRepository(client, events)), sessions);
}
