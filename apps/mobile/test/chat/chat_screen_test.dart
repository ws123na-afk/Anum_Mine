// The Assistant screen (docs/conversations.md, Mobile): every state, the
// streamed reply with Stop and Retry, the conversation list, and layouts
// from a 320 px phone at 200 percent text to a desktop-size tablet.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/chat/chat_controller.dart';
import 'package:anum_mobile/features/chat/chat_screen.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'chat_fakes.dart';

Future<void> pumpAssistant(
  WidgetTester tester,
  ChatController chat, {
  Size size = const Size(390, 844),
  double textScale = 1,
  TextDirection direction = TextDirection.ltr,
  VoidCallback? onOpenSettings,
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(context).copyWith(
          disableAnimations: true, textScaler: TextScaler.linear(textScale)),
      child: Directionality(textDirection: direction, child: child!),
    ),
    home: Scaffold(
        body:
            AssistantScreen(controller: chat, onOpenSettings: onOpenSettings)),
  ));
  await tester.pumpAndSettle();
}

FakeConversationsApi _withHistory({int extraTurns = 0}) =>
    FakeConversationsApi(conversations: [
      conversation('conv_old', 'Older chat', updatedAt: '2026-10-10T08:00:00Z'),
      conversation('conv_1', 'Quarterly plan',
          updatedAt: '2026-10-11T09:00:00Z'),
    ], turns: {
      'conv_1': [
        turnJson('turn_1', 'user', 'Plan the quarter'),
        turnJson('turn_2', 'assistant', 'Here is a plan.'),
        for (var i = 0; i < extraTurns; i++) ...[
          turnJson('q_$i', 'user', 'Follow-up question number $i'),
          turnJson('a_$i', 'assistant',
              'A longer answer number $i that wraps over a few lines on a phone screen.'),
        ],
      ],
    });

void main() {
  testWidgets('an empty workspace offers starters; a reply streams with Stop',
      (tester) async {
    final api = FakeConversationsApi();
    final events = ScriptedEvents();
    final (chat, _) = await buildChat(api, events);
    await pumpAssistant(tester, chat);

    expect(find.text('Ask anything'), findsOneWidget);
    for (final question in chatSuggestions) {
      expect(find.text(question), findsOneWidget);
    }
    expect(find.text('New conversation'), findsOneWidget);

    await tester.tap(find.text(chatSuggestions.first));
    await tester.pump();
    expect(api.where('POST', '/api/v1/conversations'), hasLength(1));
    expect(events.calls.single.$2, {'content': chatSuggestions.first});
    expect(find.text('Thinking…'), findsOneWidget);
    expect(find.text('Answering…'), findsOneWidget);
    expect(find.byTooltip('Stop'), findsOneWidget);
    expect(find.byTooltip('Send'), findsNothing);

    events.emit('turn', {
      'user_turn': turnJson('turn_1', 'user', chatSuggestions.first,
          conversationId: 'conv_new_1')
    });
    events.emit('delta', {'text': 'One approval '});
    await tester.pump();
    expect(find.textContaining('One approval'), findsOneWidget);
    events.emit('delta', {'text': 'is waiting.'});
    events.emit('done', {
      'assistant_turn': turnJson(
          'turn_2', 'assistant', 'One approval is waiting.',
          conversationId: 'conv_new_1')
    });
    await events.end();
    await tester.pumpAndSettle();

    expect(find.text('One approval is waiting.'), findsOneWidget);
    expect(find.byTooltip('Stop'), findsNothing);
    expect(find.text('Answering…'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('typing sends; Stop marks the reply stopped; Retry asks again',
      (tester) async {
    final api = _withHistory();
    final events = ScriptedEvents();
    final (chat, _) = await buildChat(api, events);
    await pumpAssistant(tester, chat);
    expect(find.text('Quarterly plan'), findsOneWidget);
    expect(find.text('Here is a plan.'), findsOneWidget);

    final send = find.byTooltip('Send');
    expect(
        tester.widget<IconButton>(find.byKey(const Key('chat-send'))).onPressed,
        isNull,
        reason: 'nothing to send yet');
    await tester.enterText(find.byKey(const Key('chat-input')), 'Tell me more');
    await tester.pump();
    await tester.tap(send);
    await tester.pump();
    expect(events.calls.single.$2, {'content': 'Tell me more'});
    events.emit(
        'turn', {'user_turn': turnJson('turn_3', 'user', 'Tell me more')});
    events.emit('delta', {'text': 'First, the goals'});
    await tester.pump();

    await tester.tap(find.byTooltip('Stop'));
    await tester.pumpAndSettle();
    expect(events.cancels, 1);
    expect(find.text('Stopped.'), findsOneWidget);
    expect(find.text('First, the goals'), findsOneWidget);

    await tester.tap(find.text('Retry'));
    await tester.pump();
    expect(events.calls.last.$2, {'retry_turn_id': 'turn_3'});
    events.emit('delta', {'text': 'Goals, then milestones.'});
    events.emit('done', {'assistant_turn': null});
    await events.end();
    await tester.pumpAndSettle();
    expect(find.text('Goals, then milestones.'), findsOneWidget);
    expect(find.text('Retry'), findsNothing);
  });

  testWidgets('a question that never reached the server is marked Not sent',
      (tester) async {
    final events = ScriptedEvents()
      ..refuseNext = const ApiException(0,
          'The server could not be reached. Check the connection and try again.');
    final (chat, _) = await buildChat(_withHistory(), events);
    await pumpAssistant(tester, chat);
    await tester.enterText(find.byKey(const Key('chat-input')), 'Hello?');
    await tester.pump();
    await tester.tap(find.byTooltip('Send'));
    await tester.pumpAndSettle();
    expect(find.text('Not sent.'), findsOneWidget);
    expect(find.textContaining('could not be reached'), findsOneWidget);
    expect(find.text('Retry'), findsNothing,
        reason: 'the question was never stored');
  });

  testWidgets('Stop before any answer says so', (tester) async {
    final events = ScriptedEvents();
    final (chat, _) = await buildChat(_withHistory(), events);
    await pumpAssistant(tester, chat);
    await tester.enterText(find.byKey(const Key('chat-input')), 'Hi');
    await tester.pump();
    await tester.tap(find.byTooltip('Send'));
    await tester.pump();
    events.emit('turn', {'user_turn': turnJson('turn_3', 'user', 'Hi')});
    await tester.pump();
    await tester.tap(find.byTooltip('Stop'));
    await tester.pumpAndSettle();
    expect(find.text('Stopped before any answer.'), findsOneWidget);
    expect(find.text('Retry'), findsOneWidget);
  });

  testWidgets('a used-up budget explains it and links to Settings',
      (tester) async {
    final events = ScriptedEvents()
      ..refuseNext = const ApiException(
          402, 'The workspace monthly model budget is used up until 1 Nov.',
          code: 'model_budget_exceeded');
    final (chat, _) = await buildChat(_withHistory(), events);
    var settings = 0;
    await pumpAssistant(tester, chat, onOpenSettings: () => settings++);
    await tester.enterText(find.byKey(const Key('chat-input')), 'Hi');
    await tester.pump();
    await tester.tap(find.byTooltip('Send'));
    await tester.pumpAndSettle();
    expect(find.text('Monthly model budget reached'), findsOneWidget);
    expect(find.textContaining('used up until 1 Nov'), findsOneWidget);
    expect(find.text('Not sent: the monthly model budget is used up.'),
        findsOneWidget);
    await tester.tap(find.text('Open Settings'));
    expect(settings, 1);
    await tester.tap(find.descendant(
        of: find.byKey(const Key('chat-budget')),
        matching: find.byTooltip('Dismiss')));
    await tester.pumpAndSettle();
    expect(find.text('Monthly model budget reached'), findsNothing);
  });

  testWidgets('a 403 explains who can give access, with Try again',
      (tester) async {
    final api = _withHistory()..listStatus = 403;
    final (chat, _) = await buildChat(api, ScriptedEvents());
    await pumpAssistant(tester, chat);
    expect(find.text('The assistant is not available to your role'),
        findsOneWidget);
    expect(find.text(chatNoPermission), findsOneWidget);

    api.listStatus = null;
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.text('Here is a plan.'), findsOneWidget);
  });

  testWidgets('a list that fails to load says so; Try again recovers',
      (tester) async {
    final api = _withHistory()..listStatus = 503;
    final (chat, _) = await buildChat(api, ScriptedEvents());
    await pumpAssistant(tester, chat, size: const Size(1024, 768));
    expect(find.text('Conversations could not be loaded'), findsWidgets);
    expect(find.textContaining('Unavailable'), findsWidgets);
    api.listStatus = null;
    await tester.tap(find.text('Try again').first);
    await tester.pumpAndSettle();
    expect(find.text('Quarterly plan'), findsWidgets);
    expect(find.text('Older chat'), findsOneWidget);
  });

  testWidgets(
      'on a phone the list opens in place: open, delete with confirm, new chat',
      (tester) async {
    final api = _withHistory();
    final (chat, _) = await buildChat(api, ScriptedEvents());
    await pumpAssistant(tester, chat);

    await tester.tap(find.byTooltip('Conversations (2)'));
    await tester.pumpAndSettle();
    expect(chat.listOpen.value, isTrue);
    expect(find.text('Conversations'), findsOneWidget);
    expect(find.byKey(const Key('chat-input')), findsNothing);
    // Newest first.
    expect(tester.getTopLeft(find.text('Quarterly plan')).dy,
        lessThan(tester.getTopLeft(find.text('Older chat')).dy));

    await tester.tap(find.text('Older chat'));
    await tester.pumpAndSettle();
    expect(chat.listOpen.value, isFalse);
    expect(chat.activeId, 'conv_old');
    expect(find.text('Ask anything'), findsOneWidget,
        reason: 'the older chat has no turns');

    await tester.tap(find.byTooltip('Conversations (2)'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Delete Quarterly plan'));
    await tester.pumpAndSettle();
    expect(find.text('Delete conversation?'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(api.where('DELETE'), isEmpty);

    await tester.tap(find.byTooltip('Delete Quarterly plan'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
    await tester.pumpAndSettle();
    expect(api.where('DELETE', '/api/v1/conversations/conv_1'), hasLength(1));
    expect(find.text('Quarterly plan'), findsNothing);
    expect(chat.listOpen.value, isTrue, reason: 'the list stays open');

    await tester.tap(find.byTooltip('New conversation'));
    await tester.pumpAndSettle();
    expect(chat.listOpen.value, isFalse);
    expect(chat.activeId, isNull);
    expect(find.text('New conversation'), findsOneWidget);
  });

  testWidgets('scrolling up stops following; Jump to latest comes back',
      (tester) async {
    final events = ScriptedEvents();
    final (chat, _) = await buildChat(_withHistory(extraTurns: 12), events);
    await pumpAssistant(tester, chat);
    final log = find.byKey(const Key('chat-log'));
    final position = tester
        .state<ScrollableState>(
            find.descendant(of: log, matching: find.byType(Scrollable)))
        .position;
    expect(position.pixels, position.maxScrollExtent,
        reason: 'a long history opens at the newest message');
    expect(find.text('Jump to latest'), findsNothing);

    await tester.drag(log, const Offset(0, 400));
    await tester.pumpAndSettle();
    expect(find.text('Jump to latest'), findsOneWidget);

    // A streaming reply does not pull the reader back down.
    final asking = chat.send('One more');
    await tester.pump();
    final before = position.pixels;
    await tester.drag(log, const Offset(0, 200));
    await tester.pump();
    events.emit('delta', {'text': 'Growing answer'});
    await tester.pump();
    expect(position.pixels, lessThan(before));
    expect(find.text('Jump to latest'), findsOneWidget);

    await tester.tap(find.text('Jump to latest'));
    await tester.pumpAndSettle();
    expect(find.text('Jump to latest'), findsNothing);
    expect(position.pixels, position.maxScrollExtent);
    events.emit('done', {'assistant_turn': null});
    await events.end();
    await tester.pumpAndSettle();
    await asking;
  });

  for (final (size, scale) in <(Size, double)>[
    (const Size(320, 568), 1),
    (const Size(320, 640), 2),
    (const Size(390, 844), 1),
    (const Size(390, 844), 2),
    (const Size(768, 1024), 1),
    (const Size(1024, 768), 1),
    (const Size(1366, 768), 1),
    (const Size(1440, 900), 1),
  ]) {
    testWidgets(
        'fits ${size.width.toInt()}x${size.height.toInt()} at ${(scale * 100).toInt()}% text',
        (tester) async {
      final events = ScriptedEvents();
      final (chat, _) = await buildChat(_withHistory(extraTurns: 3), events);
      await pumpAssistant(tester, chat, size: size, textScale: scale);
      expect(tester.takeException(), isNull);
      final wide = size.width >= assistantTwoPaneWidth;
      expect(find.text('Older chat'), wide ? findsOneWidget : findsNothing);
      expect(find.byKey(const Key('chat-input')), findsOneWidget);

      // Streaming with a long reply and the budget notice stays inside.
      final asking = chat.send('Write a long answer');
      await tester.pump();
      events.emit('delta', {'text': 'word ' * 120});
      await tester.pump();
      expect(tester.takeException(), isNull);
      events.emit('error', {
        'code': 'model_budget_exceeded',
        'message': 'The monthly model budget is used up until 1 November.',
      });
      await events.end();
      await tester.pumpAndSettle();
      await asking;
      expect(tester.takeException(), isNull);
      expect(find.text('Monthly model budget reached'), findsOneWidget);

      if (!wide) {
        await tester.tap(find.byTooltip('Conversations (2)'));
        await tester.pumpAndSettle();
        expect(find.text('Older chat'), findsOneWidget);
        expect(tester.takeException(), isNull);
      }
    });
  }

  testWidgets('Arabic right-to-left keeps the composer and bubbles in place',
      (tester) async {
    final (chat, _) = await buildChat(_withHistory(), ScriptedEvents());
    await pumpAssistant(tester, chat,
        size: const Size(360, 740),
        textScale: 1.5,
        direction: TextDirection.rtl);
    expect(tester.takeException(), isNull);
    final question = tester.getRect(find.text('Plan the quarter'));
    final answer = tester.getRect(find.text('Here is a plan.'));
    // In RTL the user's bubble sits on the left and the reply on the right.
    expect(question.center.dx, lessThan(answer.center.dx));
  });
}
