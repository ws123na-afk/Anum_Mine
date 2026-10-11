// The chat transcript's state transitions (apps/web/src/lib/chat.ts parity).
import 'package:anum_mobile/data/sse.dart';
import 'package:anum_mobile/features/chat/chat_models.dart';
import 'package:flutter_test/flutter_test.dart';

import 'chat_fakes.dart';

List<UiTurn> _apply(List<UiTurn> turns, List<SseEvent> events) =>
    events.fold(turns, applyAskEvent);

void main() {
  final stored = turnsFromStored([
    ChatTurn.tryParse(turnJson('turn_1', 'user', 'Hello'))!,
    ChatTurn.tryParse(turnJson('turn_2', 'assistant', 'Hi there'))!,
  ]);

  test('a question shows at once and the reply fills as it streams', () {
    var turns = startAsk(stored, 'What is running?');
    expect(turns.map((t) => t.status), [
      ChatTurnStatus.complete,
      ChatTurnStatus.complete,
      ChatTurnStatus.sending,
      ChatTurnStatus.streaming,
    ]);
    turns = _apply(turns, [
      SseEvent('turn',
          {'user_turn': turnJson('turn_3', 'user', 'What is running?')}),
      const SseEvent('delta', {'text': 'Two '}),
      const SseEvent('delta', {'text': 'tasks.'}),
    ]);
    expect(turns[2].id, 'turn_3');
    expect(turns[2].status, ChatTurnStatus.complete);
    expect(turns.last.content, 'Two tasks.');
    expect(turns.last.status, ChatTurnStatus.streaming);

    turns = applyAskEvent(
        turns,
        SseEvent('done',
            {'assistant_turn': turnJson('turn_4', 'assistant', 'Two tasks.')}));
    expect(turns.last.id, 'turn_4');
    expect(turns.last.status, ChatTurnStatus.complete);
    expect(canRetry(turns), isFalse);
  });

  test('done without a stored turn still settles the reply', () {
    final turns = _apply(startAsk(const [], 'Hi'), [
      const SseEvent('delta', {'text': 'Hello'}),
      const SseEvent('done', {'assistant_turn': null}),
    ]);
    expect(turns.last.status, ChatTurnStatus.complete);
    expect(turns.last.id, startsWith('local-'));
    expect(turns.last.content, 'Hello');
  });

  test('an error event marks the reply failed with its message and code', () {
    final turns = _apply(startAsk(stored, 'Again?'), [
      SseEvent('turn', {'user_turn': turnJson('turn_3', 'user', 'Again?')}),
      const SseEvent('delta', {'text': 'Partial'}),
      SseEvent('error', {
        'code': 'model_unavailable',
        'message': 'The model did not answer. Try again in a moment.',
        'assistant_turn':
            turnJson('turn_4', 'assistant', 'Partial', status: 'failed'),
      }),
    ]);
    expect(turns.last.id, 'turn_4');
    expect(turns.last.status, ChatTurnStatus.failed);
    expect(turns.last.error, contains('did not answer'));
    expect(turns.last.errorCode, 'model_unavailable');
    expect(retryTarget(turns)!.id, 'turn_3');
    expect(canRetry(turns), isTrue);
  });

  test('Stop keeps the partial reply, marked cancelled, and offers Retry', () {
    var turns = _apply(startAsk(stored, 'Long answer please'), [
      SseEvent('turn',
          {'user_turn': turnJson('turn_3', 'user', 'Long answer please')}),
      const SseEvent('delta', {'text': 'Once upon'}),
    ]);
    turns = endAsk(turns, stopped: true);
    expect(turns.last.status, ChatTurnStatus.cancelled);
    expect(turns.last.content, 'Once upon');
    expect(turns.last.id, isNot(pendingAssistantTurn));
    expect(canRetry(turns), isTrue);
  });

  test('a question the server never stored is marked failed and not retried',
      () {
    final turns = endAsk(startAsk(stored, 'Lost question'),
        stopped: false, error: 'Not answered: no connection');
    final question = turns[turns.length - 2];
    expect(question.status, ChatTurnStatus.failed);
    expect(question.id, isNot(pendingUserTurn));
    expect(turns.last.status, ChatTurnStatus.failed);
    expect(turns.last.error, 'Not answered: no connection');
    expect(retryTarget(turns), isNull);
    expect(canRetry(turns), isFalse);
  });

  test('a dropped connection says so', () {
    final turns = endAsk(
        _apply(startAsk(stored, 'Q'), [
          SseEvent('turn', {'user_turn': turnJson('turn_3', 'user', 'Q')}),
        ]),
        stopped: false);
    expect(turns.last.error, contains('connection dropped'));
  });

  test('a retry reuses the stored question and replaces the failed reply', () {
    final failed = endAsk(
        _apply(startAsk(stored, 'Q'), [
          SseEvent('turn', {'user_turn': turnJson('turn_3', 'user', 'Q')}),
        ]),
        stopped: true);
    final retrying = startAsk(failed, null);
    expect(retrying.where((t) => t.role == ChatRole.user), hasLength(2));
    expect(retrying.last.status, ChatTurnStatus.streaming);
    // The stopped reply stays above the new attempt, like the stored history.
    expect(retrying[retrying.length - 2].status, ChatTurnStatus.cancelled);
  });

  test('stored turns read their status; unknown events change nothing', () {
    final turns = turnsFromStored([
      ChatTurn.tryParse(turnJson('t1', 'assistant', '', status: 'cancelled'))!,
    ]);
    expect(turns.single.status, ChatTurnStatus.cancelled);
    expect(applyAskEvent(turns, const SseEvent('ping', 'x')), same(turns));
    expect(ChatTurn.tryParse({'id': 3}), isNull);
    expect(ChatTurn.tryParse('nope'), isNull);
  });
}
