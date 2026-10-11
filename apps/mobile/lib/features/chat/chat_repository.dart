import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/sse.dart';
import 'chat_models.dart';

/// The conversations API (docs/conversations.md). Listing and reading need
/// `task:read`; creating, asking and deleting need `task:create`.
class ChatRepository {
  const ChatRepository(this.api, this.events);

  final AnumApiClient api;
  final EventStreamClient events;

  static const _base = '/api/v1/conversations';

  /// The caller's 50 most recently active conversations, newest first.
  Future<List<Conversation>> list() async {
    final items = JsonReader(await api.request('GET', _base))
        .optObjects('data', Conversation.fromReader);
    return items..sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
  }

  Future<Conversation> create() async => Conversation.fromReader(
      JsonReader(await api.request('POST', _base, body: const {})));

  Future<ConversationDetail> get(String id) async =>
      ConversationDetail.fromReader(JsonReader(
          await api.request('GET', '$_base/${Uri.encodeComponent(id)}')));

  Future<void> delete(String id) async {
    await api.request('DELETE', '$_base/${Uri.encodeComponent(id)}');
  }

  /// Asks [content], or the stored question [retryTurnId] again; the reply
  /// streams as `turn`, `delta`, then `done` or `error`. Cancelling the
  /// subscription stops the model.
  Stream<SseEvent> ask(String id, {String? content, String? retryTurnId}) {
    assert((content == null) != (retryTurnId == null),
        'Send either content or retryTurnId');
    return events.post('$_base/${Uri.encodeComponent(id)}/ask', {
      if (content != null) 'content': content,
      if (retryTurnId != null) 'retry_turn_id': retryTurnId,
    });
  }

  /// The workspace the signed-in session works in, to notice a switch.
  Future<String?> workspaceId() async =>
      (await api.sessions.read())?.context.workspaceId;
}
