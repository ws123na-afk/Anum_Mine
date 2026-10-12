import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/sse.dart';
import 'chat_models.dart';
import 'chat_repository.dart';

enum ChatListPhase { initial, loading, ready, error }

/// Starter questions on an empty conversation, as on the web.
const chatSuggestions = [
  'What is waiting for my approval?',
  'Draft a short status update for my team',
  'Explain how approvals protect this workspace',
];

/// Shown when the caller may not decide an approval (members).
const chatCardCannotDecide = 'cannotDecide';

const chatNoPermission =
    'Your role cannot use the assistant in this workspace. Ask a workspace '
    'owner or admin to give you the member role.';

/// What went wrong, in words that say how to fix it.
String explainChatError(Object error, String action) {
  if (error is ApiException && error.isPermissionDenied) {
    return chatNoPermission;
  }
  final reason = switch (error) {
    ApiException(statusCode: 401) =>
      'your sign-in expired. Sign in again to keep chatting.',
    ApiException(statusCode: 404) =>
      'this conversation no longer exists. Start a new one.',
    ApiException(statusCode: 0, :final message) => message,
    ApiException(isBudgetExceeded: true, :final message) =>
      'the monthly model budget is used up. $message',
    ApiException(:final message) => message,
    EventStreamTimeoutException(:final message) => message,
    FormatException() => 'the server sent an answer this app cannot read.',
    _ => 'the server could not be reached. Check the connection and try again.',
  };
  return '$action: $reason';
}

/// The Assistant: a streamed chat that remembers the conversation
/// (docs/conversations.md). Mirrors the web `ChatView`.
class ChatController extends ChangeNotifier {
  ChatController(this.repository);

  final ChatRepository repository;

  List<Conversation> conversations = const [];
  ChatListPhase listPhase = ChatListPhase.initial;
  String? listError;

  /// The open conversation; null for a new one (created on the first ask).
  String? activeId;
  List<UiTurn> turns = const [];

  /// A reply is streaming; Stop is offered and the list is locked.
  bool streaming = false;

  /// The open conversation's turns are loading.
  bool opening = false;

  /// Something that failed outside a turn (opening or deleting).
  String? notice;

  /// Set when an ask is refused because the monthly model budget is used up.
  String? budget;

  /// On phones the conversation list replaces the chat while open; Back
  /// closes it (the workspace's PopScope reads this).
  final ValueNotifier<bool> listOpen = ValueNotifier(false);

  StreamSubscription<SseEvent>? _subscription;
  Completer<void>? _streamDone;
  bool _stopRequested = false;
  bool _disposed = false;
  int _openToken = 0;
  String? _workspaceId;

  Conversation? get active {
    for (final conversation in conversations) {
      if (conversation.id == activeId) return conversation;
    }
    return null;
  }

  String get title => active?.displayTitle ?? 'New conversation';

  bool get canRetryLast => !streaming && canRetry(turns);

  /// Loads the list once, opening the newest conversation.
  Future<void> ensureLoaded() async {
    if (listPhase == ChatListPhase.initial) await load(openFirst: true);
  }

  Future<void> load({bool openFirst = false}) async {
    listPhase = ChatListPhase.loading;
    listError = null;
    _notify();
    try {
      _workspaceId = await repository.workspaceId();
      final items = await repository.list();
      if (_disposed) return;
      conversations = items;
      listPhase = ChatListPhase.ready;
      _notify();
      if (openFirst && activeId == null && turns.isEmpty && items.isNotEmpty) {
        await open(items.first.id);
      }
    } on Object catch (error) {
      if (_disposed) return;
      listPhase = ChatListPhase.error;
      listError = explainChatError(error, 'Conversations could not be loaded');
      _notify();
    }
  }

  /// Starts over when the signed-in workspace changed since the list loaded.
  Future<void> syncWorkspace() async {
    if (listPhase == ChatListPhase.initial) return;
    final current = await repository.workspaceId();
    if (_disposed || current == _workspaceId) return;
    reset();
    await load(openFirst: true);
  }

  /// Forgets everything (sign-out or a workspace switch).
  void reset() {
    stop();
    _openToken++;
    conversations = const [];
    listPhase = ChatListPhase.initial;
    listError = null;
    activeId = null;
    turns = const [];
    opening = false;
    notice = null;
    budget = null;
    _workspaceId = null;
    listOpen.value = false;
    _notify();
  }

  /// Opens [id], or a new empty conversation when null.
  Future<void> open(String? id) async {
    stop();
    final token = ++_openToken;
    activeId = id;
    notice = null;
    budget = null;
    listOpen.value = false;
    if (id == null) {
      turns = const [];
      opening = false;
      _notify();
      return;
    }
    turns = const [];
    opening = true;
    _notify();
    try {
      final detail = await repository.get(id);
      if (_disposed || token != _openToken) return;
      turns = turnsFromStored(detail.turns, detail.reports);
    } on Object catch (error) {
      if (_disposed || token != _openToken) return;
      turns = const [];
      notice = explainChatError(error, 'This conversation could not be loaded');
    }
    opening = false;
    _notify();
  }

  void startNew() => unawaited(open(null));

  Future<void> send(String content) async {
    final question = content.trim();
    if (question.isEmpty || streaming) return;
    await _ask(content: question);
  }

  Future<void> retry() async {
    final target = retryTarget(turns);
    if (target == null || streaming) return;
    await _ask(retryTurnId: target.id);
  }

  /// Stop: closes the request, which stops the model on the server; the
  /// partial reply stays, marked stopped.
  void stop() {
    if (!streaming) return;
    _stopRequested = true;
    final subscription = _subscription;
    _subscription = null;
    if (subscription != null) unawaited(subscription.cancel());
    final done = _streamDone;
    if (done != null && !done.isCompleted) done.complete();
  }

  Future<void> _ask({String? content, String? retryTurnId}) async {
    if (streaming) return;
    notice = null;
    budget = null;
    _stopRequested = false;
    turns = startAsk(turns, content);
    streaming = true;
    _notify();
    final token = _openToken;
    var ended = false;
    try {
      var id = activeId;
      if (id == null) {
        final created = await repository.create();
        if (_disposed) return;
        id = created.id;
        activeId = id;
        conversations = [
          created,
          for (final c in conversations)
            if (c.id != created.id) c
        ];
        _notify();
      }
      if (!_stopRequested) {
        final done = _streamDone = Completer<void>();
        _subscription = repository
            .ask(id, content: content, retryTurnId: retryTurnId)
            .listen(
          (event) {
            if (_disposed || token != _openToken) return;
            if (event.event == 'done' || event.event == 'error') ended = true;
            final data = event.fields;
            if (event.event == 'error' &&
                data['code'] == 'model_budget_exceeded') {
              final message = data['message'];
              budget = message is String
                  ? message
                  : 'The monthly model budget is used up.';
            }
            turns = applyAskEvent(turns, event);
            _notify();
          },
          onError: (Object error, StackTrace stack) {
            if (!done.isCompleted) done.completeError(error, stack);
          },
          onDone: () {
            if (!done.isCompleted) done.complete();
          },
          cancelOnError: true,
        );
        await done.future;
      }
      if (_disposed || token != _openToken) return;
      if (_stopRequested) {
        turns = endAsk(turns, stopped: true);
      } else if (!ended) {
        turns = endAsk(turns, stopped: false);
      }
    } on Object catch (error) {
      if (_disposed || token != _openToken) return;
      if (_stopRequested) {
        turns = endAsk(turns, stopped: true);
      } else if (error is ApiException && error.isBudgetExceeded) {
        budget = error.message;
        turns = endAsk(turns,
            stopped: false,
            error: 'Not sent: the monthly model budget is used up.',
            errorCode: 'model_budget_exceeded');
      } else {
        turns = endAsk(turns,
            stopped: false, error: explainChatError(error, 'Not answered'));
      }
    } finally {
      _subscription = null;
      _streamDone = null;
      _stopRequested = false;
      streaming = false;
      _notify();
      if (!_disposed) unawaited(_refreshList());
    }
  }

  /// The list keeps its last good state when a refresh fails.
  Future<void> _refreshList() async {
    try {
      final items = await repository.list();
      if (_disposed) return;
      conversations = items;
      if (listPhase != ChatListPhase.ready) {
        listPhase = ChatListPhase.ready;
        listError = null;
      }
      _notify();
    } on Object {
      // Keep what is shown.
    }
  }

  /// Deletes [conversation] and its turns; opening the newest left when it
  /// was the open one.
  Future<bool> delete(Conversation conversation) async {
    if (streaming) return false;
    notice = null;
    _notify();
    try {
      await repository.delete(conversation.id);
    } on Object catch (error) {
      if (_disposed) return false;
      notice = explainChatError(error, 'The conversation could not be deleted');
      _notify();
      return false;
    }
    if (_disposed) return true;
    conversations = [
      for (final c in conversations)
        if (c.id != conversation.id) c
    ];
    if (activeId == conversation.id) {
      final next = conversations.isEmpty ? null : conversations.first.id;
      final keepList = listOpen.value;
      await open(next);
      listOpen.value = keepList;
    } else {
      _notify();
    }
    return true;
  }

  /// The approval card being decided (its turn id), while the call runs.
  String? deciding;

  /// Why the last decision on [decidingError]'s turn failed.
  String? decisionError;
  String? decisionErrorTurn;

  /// Approve or Reject on a card: the ordinary approvals route with the
  /// card's payload hash; the card then shows what happened.
  Future<void> decide(String turnId, {required bool approve}) async {
    final index = turns.indexWhere((turn) => turn.id == turnId);
    final approval = index < 0 ? null : turns[index].card?.approval;
    if (approval == null || deciding != null) return;
    deciding = turnId;
    decisionError = null;
    decisionErrorTurn = null;
    _notify();
    try {
      final response = await repository.decide(approval, approve: approve);
      if (_disposed) return;
      turns = [
        for (final turn in turns)
          turn.id == turnId && turn.card != null
              ? turn.copyWith(card: turn.card!.afterDecision(response))
              : turn
      ];
    } on Object catch (error) {
      if (_disposed) return;
      decisionErrorTurn = turnId;
      decisionError = error is ApiException && error.isPermissionDenied
          ? chatCardCannotDecide
          : explainChatError(error, 'The decision was not recorded');
    } finally {
      deciding = null;
      _notify();
    }
  }

  void dismissBudget() {
    budget = null;
    _notify();
  }

  void dismissNotice() {
    notice = null;
    _notify();
  }

  void showList() => listOpen.value = true;
  void closeList() => listOpen.value = false;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    stop();
    _disposed = true;
    listOpen.dispose();
    super.dispose();
  }
}
