// The Assistant chat controller against the conversations API
// (docs/conversations.md), with a scripted reply stream.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/sse.dart';
import 'package:anum_mobile/features/chat/chat_controller.dart';
import 'package:anum_mobile/features/chat/chat_models.dart';
import 'package:flutter_test/flutter_test.dart';

import 'chat_fakes.dart';

void main() {
  late FakeConversationsApi api;
  late ScriptedEvents events;

  setUp(() {
    api = FakeConversationsApi(conversations: [
      conversation('conv_old', 'Older chat', updatedAt: '2026-10-10T08:00:00Z'),
      conversation('conv_1', 'Quarterly plan',
          updatedAt: '2026-10-11T09:00:00Z'),
    ], turns: {
      'conv_1': [
        turnJson('turn_1', 'user', 'Plan the quarter'),
        turnJson('turn_2', 'assistant', 'Here is a plan.'),
      ],
    });
    events = ScriptedEvents();
  });

  test('loads the list newest first and opens the newest conversation',
      () async {
    final (chat, _) = await buildChat(api, events);
    await chat.ensureLoaded();
    expect(chat.listPhase, ChatListPhase.ready);
    expect(chat.conversations.map((c) => c.id), ['conv_1', 'conv_old']);
    expect(chat.activeId, 'conv_1');
    expect(chat.title, 'Quarterly plan');
    expect(chat.turns.map((t) => t.content),
        ['Plan the quarter', 'Here is a plan.']);
    await chat.ensureLoaded();
    expect(api.where('GET', '/api/v1/conversations'), hasLength(1));
  });

  test('the first question creates a conversation, then the reply streams',
      () async {
    api.conversations.clear();
    final (chat, _) = await buildChat(api, events);
    await chat.ensureLoaded();
    expect(chat.activeId, isNull);

    final asking = chat.send('  What is running?  ');
    await pumpEventQueue();
    expect(api.where('POST', '/api/v1/conversations'), hasLength(1));
    expect(chat.activeId, 'conv_new_1');
    expect(events.calls.single.$1, '/api/v1/conversations/conv_new_1/ask');
    expect(events.calls.single.$2, {'content': 'What is running?'});
    expect(chat.streaming, isTrue);
    expect(chat.turns.last.status, ChatTurnStatus.streaming);

    events.emit(
        'turn', {'user_turn': turnJson('turn_9', 'user', 'What is running?')});
    events.emit('delta', {'text': 'Two tasks'});
    await pumpEventQueue();
    expect(chat.turns.last.content, 'Two tasks');
    events.emit('delta', {'text': ' are running.'});
    events.emit('done', {
      'assistant_turn':
          turnJson('turn_10', 'assistant', 'Two tasks are running.')
    });
    await events.end();
    await asking;

    expect(chat.streaming, isFalse);
    expect(chat.turns.map((t) => t.id), ['turn_9', 'turn_10']);
    expect(chat.turns.last.status, ChatTurnStatus.complete);
    expect(chat.canRetryLast, isFalse);
    // The list is refreshed after every reply.
    await pumpEventQueue();
    expect(api.where('GET', '/api/v1/conversations'), hasLength(2));
  });

  test(
      'Stop cancels the stream and keeps the partial reply, then Retry asks again',
      () async {
    final (chat, _) = await buildChat(api, events);
    await chat.ensureLoaded();
    final asking = chat.send('Tell me a long story');
    await pumpEventQueue();
    events.emit('turn',
        {'user_turn': turnJson('turn_3', 'user', 'Tell me a long story')});
    events.emit('delta', {'text': 'Once upon a time'});
    await pumpEventQueue();

    chat.stop();
    await asking;
    expect(events.cancels, 1);
    expect(chat.streaming, isFalse);
    expect(chat.turns.last.status, ChatTurnStatus.cancelled);
    expect(chat.turns.last.content, 'Once upon a time');
    expect(chat.canRetryLast, isTrue);

    final retrying = chat.retry();
    await pumpEventQueue();
    expect(events.calls.last.$2, {'retry_turn_id': 'turn_3'});
    events.emit('delta', {'text': 'Once upon a time, again.'});
    events.emit('done', {'assistant_turn': null});
    await events.end();
    await retrying;
    expect(chat.turns.where((t) => t.role == ChatRole.user), hasLength(2));
    expect(chat.turns.last.status, ChatTurnStatus.complete);
  });

  test('Stop before anything arrives marks the reply stopped before any answer',
      () async {
    final (chat, _) = await buildChat(api, events);
    await chat.ensureLoaded();
    final asking = chat.send('Quick one');
    await pumpEventQueue();
    chat.stop();
    await asking;
    final question = chat.turns[chat.turns.length - 2];
    expect(question.status, ChatTurnStatus.failed, reason: 'never stored');
    expect(chat.turns.last.status, ChatTurnStatus.cancelled);
    expect(chat.turns.last.content, isEmpty);
  });

  test('a 402 refusal shows the budget message and marks the question not sent',
      () async {
    final (chat, _) = await buildChat(api, events);
    await chat.ensureLoaded();
    events.refuseNext = const ApiException(
        402, 'The workspace monthly model budget is used up until 1 Nov.',
        code: 'model_budget_exceeded');
    await chat.send('Anything');
    expect(chat.budget, contains('1 Nov'));
    expect(chat.turns[chat.turns.length - 2].status, ChatTurnStatus.failed);
    expect(chat.turns.last.error,
        'Not sent: the monthly model budget is used up.');
    expect(chat.turns.last.errorCode, 'model_budget_exceeded');
    chat.dismissBudget();
    expect(chat.budget, isNull);
  });

  test('a budget error event mid-stream also shows the budget message',
      () async {
    final (chat, _) = await buildChat(api, events);
    await chat.ensureLoaded();
    final asking = chat.send('Anything');
    await pumpEventQueue();
    events.emit('turn', {'user_turn': turnJson('turn_3', 'user', 'Anything')});
    events.emit('error', {
      'code': 'model_budget_exceeded',
      'message': 'Budget used up until 1 Nov.',
      'assistant_turn': turnJson('turn_4', 'assistant', '', status: 'failed'),
    });
    await events.end();
    await asking;
    expect(chat.budget, 'Budget used up until 1 Nov.');
    expect(chat.turns.last.status, ChatTurnStatus.failed);
    expect(chat.canRetryLast, isTrue);
  });

  test('a 403 explains who can give access', () async {
    api.listStatus = 403;
    final (chat, _) = await buildChat(api, events);
    await chat.ensureLoaded();
    expect(chat.listPhase, ChatListPhase.error);
    expect(chat.listError, chatNoPermission);

    api.listStatus = null;
    await chat.load(openFirst: true);
    expect(chat.listPhase, ChatListPhase.ready);
    expect(chat.activeId, 'conv_1');
  });

  test('a connection dropped mid-reply says so and offers Retry', () async {
    final (chat, _) = await buildChat(api, events);
    await chat.ensureLoaded();
    final asking = chat.send('Hello?');
    await pumpEventQueue();
    events.emit('turn', {'user_turn': turnJson('turn_3', 'user', 'Hello?')});
    events.fail(const EventStreamTimeoutException(
        'The answer stopped arriving. Check the connection and try again.'));
    await asking;
    expect(chat.turns.last.error,
        'Not answered: The answer stopped arriving. Check the connection and try again.');
    expect(chat.canRetryLast, isTrue);
  });

  test('deleting the open conversation opens the newest one left', () async {
    final (chat, _) = await buildChat(api, events);
    await chat.ensureLoaded();
    final deleted = await chat.delete(chat.active!);
    expect(deleted, isTrue);
    expect(api.where('DELETE', '/api/v1/conversations/conv_1'), hasLength(1));
    expect(chat.conversations.map((c) => c.id), ['conv_old']);
    expect(chat.activeId, 'conv_old');

    api.deleteStatus = 500;
    expect(await chat.delete(chat.active!), isFalse);
    expect(chat.notice, 'The conversation could not be deleted: Database down');
    expect(chat.conversations, hasLength(1));
  });

  test('a failed open says what failed', () async {
    final (chat, _) = await buildChat(api, events);
    await chat.ensureLoaded();
    await chat.open('conv_gone');
    expect(chat.turns, isEmpty);
    expect(chat.notice, contains('no longer exists'));
  });

  test('a workspace switch starts over; sign-out forgets everything', () async {
    final (chat, sessions) = await buildChat(api, events);
    await chat.ensureLoaded();
    await chat.syncWorkspace();
    expect(api.where('GET', '/api/v1/conversations'), hasLength(1));

    final other = await signedInSessions(workspaceId: 'workspace_other');
    await sessions.write((await other.read())!);
    await chat.syncWorkspace();
    expect(api.where('GET', '/api/v1/conversations'), hasLength(2));

    chat.reset();
    expect(chat.conversations, isEmpty);
    expect(chat.turns, isEmpty);
    expect(chat.listPhase, ChatListPhase.initial);
  });

  test('switching conversations while answering stops the reply', () async {
    final (chat, _) = await buildChat(api, events);
    await chat.ensureLoaded();
    final asking = chat.send('First');
    await pumpEventQueue();
    await chat.open('conv_old');
    await asking;
    expect(events.cancels, 1);
    expect(chat.streaming, isFalse);
    expect(chat.activeId, 'conv_old');
  });

  test('JSON helpers read conversation turns tolerantly', () {
    final turn = ChatTurn.tryParse(JsonReader(
            {'id': 'x', 'role': 'assistant', 'content': 'y', 'status': 'weird'})
        .json);
    expect(turn!.status, ChatTurnStatus.complete);
  });
}
