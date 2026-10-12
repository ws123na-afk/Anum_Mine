/// The Assistant chat's data and its transcript rules (docs/conversations.md),
/// mirroring the web client's `apps/web/src/lib/chat.ts`. Pure Dart, so the
/// state transitions are unit tested without widgets.
library;

import '../../data/api_models.dart';
import '../../data/sse.dart';
import '../library/library_models.dart' show ReportCard;
import 'chat_cards.dart';

enum ChatRole { user, assistant }

/// A turn's state: the stored ones, plus the question being sent and the
/// reply being streamed.
enum ChatTurnStatus { complete, cancelled, failed, sending, streaming }

ChatTurnStatus _storedStatus(String? value) => switch (value) {
      'cancelled' => ChatTurnStatus.cancelled,
      'failed' => ChatTurnStatus.failed,
      _ => ChatTurnStatus.complete,
    };

/// `GET /api/v1/conversations` items.
class Conversation {
  const Conversation({
    required this.id,
    required this.title,
    required this.createdAt,
    required this.updatedAt,
  });

  factory Conversation.fromReader(JsonReader r) => Conversation(
        id: r.string('id'),
        title: r.optString('title') ?? '',
        createdAt: r.date('created_at'),
        updatedAt: r.date('updated_at'),
      );

  final String id;
  final String title;
  final DateTime createdAt;
  final DateTime updatedAt;

  /// The API names a conversation after its first question.
  String get displayTitle =>
      title.trim().isEmpty ? 'New conversation' : title.trim();
}

/// A stored turn.
class ChatTurn {
  const ChatTurn({
    required this.id,
    required this.role,
    required this.content,
    required this.status,
    this.card,
  });

  factory ChatTurn.fromReader(JsonReader r) => ChatTurn(
        id: r.string('id'),
        role: r.string('role') == 'user' ? ChatRole.user : ChatRole.assistant,
        content: r.optString('content') ?? '',
        status: _storedStatus(r.optString('status')),
        card: ChatCard.tryParse(r.json['card']),
      );

  /// A turn carried by an event, or null when the event has none.
  static ChatTurn? tryParse(Object? value) {
    if (value is! Map) return null;
    try {
      return ChatTurn.fromReader(JsonReader.of(value, 'turn'));
    } on FormatException {
      return null;
    }
  }

  final String id;
  final ChatRole role;
  final String content;
  final ChatTurnStatus status;

  /// The card of a question run as a task; null for plain conversation.
  final ChatCard? card;
}

/// `GET /api/v1/conversations/{id}`: the conversation and its turns, oldest
/// first.
class ConversationDetail {
  const ConversationDetail(
      {required this.conversation,
      required this.turns,
      this.reports = const []});

  factory ConversationDetail.fromReader(JsonReader r) => ConversationDetail(
        conversation: Conversation.fromReader(r),
        turns: r.optObjects('turns', ChatTurn.fromReader),
        reports: r.optObjects('reports', ReportCard.fromReader),
      );

  final Conversation conversation;
  final List<ChatTurn> turns;

  /// Cards for the Assistant's structured answers (docs/reports.md).
  final List<ReportCard> reports;
}

/// A turn as the chat shows it.
class UiTurn {
  const UiTurn({
    required this.id,
    required this.role,
    required this.content,
    required this.status,
    this.error,
    this.errorCode,
    this.report,
    this.card,
  });

  final String id;
  final ChatRole role;
  final String content;
  final ChatTurnStatus status;

  /// What failed, in words that say how to fix it.
  final String? error;

  /// The stream's error code, e.g. `model_budget_exceeded`.
  final String? errorCode;

  /// A structured answer's report card ("View Full Report").
  final ReportCard? report;

  /// The task card (docs/conversations.md#tools-in-chat), when there is one.
  final ChatCard? card;

  /// Only a results card shows the written answer above it; other cards
  /// speak for themselves, localized.
  bool get showsText => card == null || card!.state == ChatCardState.results;

  UiTurn copyWith({
    String? id,
    String? content,
    ChatTurnStatus? status,
    String? error,
    String? errorCode,
    ChatCard? card,
  }) =>
      UiTurn(
        id: id ?? this.id,
        role: role,
        content: content ?? this.content,
        status: status ?? this.status,
        error: error ?? this.error,
        errorCode: errorCode ?? this.errorCode,
        report: report,
        card: card ?? this.card,
      );

  @override
  String toString() => 'UiTurn($id, ${role.name}, ${status.name}, "$content")';
}

const pendingUserTurn = 'pending-user';
const pendingAssistantTurn = 'pending-assistant';
int _localTurns = 0;
String _localId() => 'local-${++_localTurns}';

UiTurn _fromTurn(ChatTurn turn, {ReportCard? report, ChatCard? fallback}) =>
    UiTurn(
        id: turn.id,
        role: turn.role,
        content: turn.content,
        status: turn.status,
        report: report,
        card: turn.card ?? fallback);

List<UiTurn> turnsFromStored(List<ChatTurn> turns,
    [List<ReportCard> reports = const []]) {
  final cards = {
    for (final card in reports)
      if (card.turnId != null) card.turnId!: card
  };
  return [for (final turn in turns) _fromTurn(turn, report: cards[turn.id])];
}

/// Shows the question at once and an empty reply that fills as it streams.
/// A retry ([question] null) reuses the stored question.
List<UiTurn> startAsk(List<UiTurn> turns, String? question) => [
      for (final turn in turns)
        if (turn.status != ChatTurnStatus.streaming &&
            turn.status != ChatTurnStatus.sending)
          turn,
      if (question != null)
        UiTurn(
            id: pendingUserTurn,
            role: ChatRole.user,
            content: question,
            status: ChatTurnStatus.sending),
      const UiTurn(
          id: pendingAssistantTurn,
          role: ChatRole.assistant,
          content: '',
          status: ChatTurnStatus.streaming),
    ];

/// Applies one event of `POST /conversations/{id}/ask` to the transcript.
List<UiTurn> applyAskEvent(List<UiTurn> turns, SseEvent event) {
  final data = event.fields;
  switch (event.event) {
    case 'turn':
      final user = ChatTurn.tryParse(data['user_turn']);
      if (user == null) return turns;
      return [
        for (final turn in turns)
          turn.id == pendingUserTurn ? _fromTurn(user) : turn
      ];
    case 'tool':
      final card = ChatCard.tryParse(data['card']);
      if (card == null) return turns;
      return [
        for (final turn in turns)
          turn.id == pendingAssistantTurn ? turn.copyWith(card: card) : turn
      ];
    case 'delta':
      final text = data['text'];
      if (text is! String) return turns;
      return [
        for (final turn in turns)
          turn.id == pendingAssistantTurn
              ? turn.copyWith(content: turn.content + text)
              : turn
      ];
    case 'done':
      final reply = ChatTurn.tryParse(data['assistant_turn']);
      final report = ReportCard.tryParse(data['report']);
      return [
        for (final turn in turns)
          turn.id == pendingAssistantTurn
              ? reply != null
                  ? _fromTurn(reply, report: report, fallback: turn.card)
                  : turn.copyWith(
                      id: _localId(), status: ChatTurnStatus.complete)
              : turn
      ];
    case 'error':
      final reply = ChatTurn.tryParse(data['assistant_turn']);
      final message = data['message'];
      final code = data['code'];
      return [
        for (final turn in turns)
          turn.id == pendingAssistantTurn
              ? UiTurn(
                  id: reply?.id ?? _localId(),
                  role: ChatRole.assistant,
                  content: reply?.content ?? turn.content,
                  status: ChatTurnStatus.failed,
                  error: message is String && message.isNotEmpty
                      ? message
                      : 'The model did not answer.',
                  errorCode: code is String ? code : null,
                  card: turn.card)
              : turn
      ];
  }
  return turns;
}

/// The request ended without `done` or `error`: Stop, a dropped connection,
/// or an HTTP error before the stream started.
List<UiTurn> endAsk(List<UiTurn> turns,
    {required bool stopped, String? error, String? errorCode}) {
  return [
    for (final turn in turns)
      if (turn.id == pendingUserTurn)
        // A question the server never stored stays visible, marked "Not sent".
        turn.copyWith(id: _localId(), status: ChatTurnStatus.failed)
      else if (turn.status == ChatTurnStatus.streaming)
        stopped
            ? turn.copyWith(id: _localId(), status: ChatTurnStatus.cancelled)
            : turn.copyWith(
                id: _localId(),
                status: ChatTurnStatus.failed,
                error: error ??
                    'The connection dropped before the answer finished.',
                errorCode: errorCode)
      else
        turn
  ];
}

/// The latest stored question, which Retry asks again; null when there is
/// none or it was never stored.
UiTurn? retryTarget(List<UiTurn> turns) {
  for (final turn in turns.reversed) {
    if (turn.role != ChatRole.user) continue;
    return turn.id == pendingUserTurn ||
            turn.status == ChatTurnStatus.failed ||
            turn.id.startsWith('local-')
        ? null
        : turn;
  }
  return null;
}

/// Retry is offered when the last reply failed or was stopped.
bool canRetry(List<UiTurn> turns) {
  if (turns.isEmpty) return false;
  final last = turns.last;
  return last.role == ChatRole.assistant &&
      (last.status == ChatTurnStatus.failed ||
          last.status == ChatTurnStatus.cancelled) &&
      retryTarget(turns) != null;
}
