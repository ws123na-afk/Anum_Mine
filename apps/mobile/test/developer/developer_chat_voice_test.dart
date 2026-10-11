// Developer mode from the Assistant and by voice (docs/developer-mode.md):
// "build ..." / "developer, ..." is confirmed first and never reaches the
// model; voice never merges, "approve" only opens the approval with its hash.
import 'package:anum_mobile/features/chat/chat_controller.dart';
import 'package:anum_mobile/features/chat/chat_screen.dart';
import 'package:anum_mobile/features/developer/developer_assist.dart';
import 'package:anum_mobile/features/developer/developer_models.dart';
import 'package:anum_mobile/features/developer/developer_repository.dart';
import 'package:anum_mobile/features/voice/voice_models.dart';
import 'package:anum_mobile/features/voice/voice_preferences.dart';
import 'package:anum_mobile/features/voice/voice_screen.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../chat/chat_fakes.dart';
import '../voice/voice_fakes.dart';
import 'developer_fakes.dart';

Future<DeveloperAssist> _assist(FakeDeveloperApi api, String source) async =>
    DeveloperAssist(
      repository: ApiDeveloperRepository(await developerApi(api)),
      source: source,
      isOwner: () async => api.role == 'owner',
    );

Future<void> _pump(WidgetTester tester, Widget body,
    {Size size = const Size(390, 844), double textScale = 1}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(context).copyWith(
          disableAnimations: true, textScaler: TextScaler.linear(textScale)),
      child: child!,
    ),
    home: Scaffold(body: body),
  ));
  await tester.pumpAndSettle();
}

Future<void> _chatSend(WidgetTester tester, String text) async {
  await tester.enterText(find.byKey(const Key('chat-input')), text);
  await tester.pump();
  await tester.tap(find.byKey(const Key('chat-send')));
  await tester.pumpAndSettle();
}

Future<(ChatController, ScriptedEvents)> _chat() async {
  final events = ScriptedEvents();
  final (chat, _) = await buildChat(FakeConversationsApi(), events);
  return (chat, events);
}

void main() {
  group('Assistant', () {
    testWidgets('"build ..." asks first; Start creates the request',
        (tester) async {
      final (chat, events) = await _chat();
      final api = FakeDeveloperApi();
      final assist = await _assist(api, 'text');
      var opened = 0;
      await _pump(
          tester,
          AssistantScreen(
              controller: chat,
              developer: assist,
              onOpenDeveloper: () => opened++));

      await _chatSend(tester, 'build a weekly digest email for owners');
      expect(
          find.text("I'll ask the developer to: a weekly digest email for "
              'owners in ANUM. Start?'),
          findsOneWidget);
      expect(events.calls, isEmpty, reason: 'never sent to the model');
      expect(api.calls('POST', '/developer/requests'), isEmpty);

      await tester.tap(find.byKey(const Key('developer-start')));
      await tester.pumpAndSettle();
      final body = api.calls('POST', '/developer/requests').single.body!;
      expect(body['source'], 'text');
      expect(body['text'], 'a weekly digest email for owners');
      expect(find.textContaining('Started in ANUM. The developer is on it'),
          findsOneWidget);
      await tester.tap(find.text('Open Developer'));
      expect(opened, 1);
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('Cancel declines; ordinary questions still reach the model',
        (tester) async {
      final (chat, events) = await _chat();
      final api = FakeDeveloperApi();
      final assist = await _assist(api, 'text');
      await _pump(tester, AssistantScreen(controller: chat, developer: assist));
      await _chatSend(tester, 'developer, add a search box to Files');
      await tester.tap(find.byKey(const Key('developer-decline')));
      await tester.pumpAndSettle();
      expect(find.text("Okay, I won't start it."), findsOneWidget);
      expect(api.calls('POST', '/developer/requests'), isEmpty);

      await _chatSend(tester, 'What is waiting for my approval?');
      expect(events.calls, hasLength(1));
      await events.end();
      await tester.pumpAndSettle();
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('non-owners get a polite refusal', (tester) async {
      final (chat, events) = await _chat();
      final api = FakeDeveloperApi(role: 'member');
      final assist = await _assist(api, 'text');
      await _pump(tester, AssistantScreen(controller: chat, developer: assist));
      await _chatSend(tester, 'build a weekly digest email for owners');
      expect(find.text(ownerOnlyReply), findsOneWidget);
      expect(events.calls, isEmpty);
      expect(api.calls('POST', '/developer/'), isEmpty);
      await tester.pumpWidget(const SizedBox());
    });

    for (final (size, scale) in [
      (const Size(320, 568), 2.0),
      (const Size(390, 844), 1.0),
      (const Size(1440, 900), 1.0),
    ]) {
      testWidgets(
          'the confirmation and the merge card fit '
          '${size.width.toInt()}x${size.height.toInt()} at '
          '${(scale * 100).toInt()}%', (tester) async {
        final (chat, _) = await _chat();
        final api = FakeDeveloperApi(requests: [
          requestJson('dev_1', status: 'waiting_approval', pr: 42)
        ]);
        final assist = await _assist(api, 'text');
        await _pump(
            tester, AssistantScreen(controller: chat, developer: assist),
            size: size, textScale: scale);
        await _chatSend(tester, 'build a weekly digest email for owners');
        expect(tester.takeException(), isNull);
        await assist.intercept('no');
        await assist.intercept('approve');
        await tester.pumpAndSettle();
        expect(find.byKey(const Key('developer-merge-review')), findsOneWidget);
        expect(tester.takeException(), isNull);
        await tester.pumpWidget(const SizedBox());
      });
    }
  });

  group('voice', () {
    test('"developer, ..." asks, listens for the answer, and "yes" starts it',
        () async {
      final speech = FakeSpeech();
      final transport = FakeVoiceTransport();
      final voice = await buildController(speech: speech, transport: transport);
      final api = FakeDeveloperApi();
      voice.developer = await _assist(api, 'voice');
      await voice.load();
      voice.setVisible(true);
      await pumpEventQueue();
      final listensBefore = speech.listens.length;

      await voice.ask('Developer, add a dark mode toggle to Settings');
      expect(voice.turns.last.speaker, VoiceSpeaker.assistant);
      expect(
          voice.turns.last.text,
          "I'll ask the developer to: add a dark mode toggle to Settings in "
          'ANUM. Start?');
      expect(speech.spoken.last, voice.turns.last.text);
      expect(transport.requests, isEmpty,
          reason: 'developer commands never reach the voice API');
      expect(speech.listens.length, greaterThan(listensBefore));
      expect(voice.followUp, isTrue, reason: 'answers without the name');

      await voice.ask('yes');
      final body = api.calls('POST', '/developer/requests').single.body!;
      expect(body['source'], 'voice');
      expect(voice.turns.last.text, startsWith('Started in ANUM.'));
      voice.setVisible(false);
      voice.dispose();
    });

    test('"approve" opens the approval with its hash and never merges',
        () async {
      final speech = FakeSpeech();
      final voice = await buildController(
          speech: speech,
          transport: FakeVoiceTransport(),
          preferences: MemoryVoicePreferences({'anum.voice.wake': 'off'}));
      final api = FakeDeveloperApi(requests: [
        requestJson('dev_1', status: 'ci_running', pr: 42, prTitle: 'Dark mode')
      ]);
      final assist = await _assist(api, 'voice');
      assist.announce = (text) => voice.sayLocal(text);
      voice.developer = assist;
      voice.setVisible(true);
      await assist.poll();
      api.requests[0] = requestJson('dev_1',
          status: 'waiting_approval', pr: 42, prTitle: 'Dark mode');
      await assist.poll();
      await pumpEventQueue();
      expect(speech.spoken.last,
          'The change is ready for review in ANUM: Dark mode. Say approve to merge.');

      await voice.ask('approve it');
      expect(voice.turns.last.text, contains('payload hash'));
      expect(voice.turns.last.text, contains('I never approve by voice'));
      expect(assist.review!.approval.payloadHash, exampleHash);
      expect(api.calls('POST', '/approve'), isEmpty);
      await voice.ask('yes');
      expect(api.calls('POST', '/approve'), isEmpty,
          reason: 'a spoken yes is not a tap on Confirm merge');
      voice.setVisible(false);
      voice.dispose();
    });

    test('non-owners hear a polite refusal', () async {
      final speech = FakeSpeech();
      final voice = await buildController(
          speech: speech, transport: FakeVoiceTransport());
      final api = FakeDeveloperApi(role: 'member');
      voice.developer = await _assist(api, 'voice');
      await voice.ask('Anum build a weekly digest email');
      expect(voice.turns.last.text, ownerOnlyReply);
      expect(api.calls('POST', '/developer/'), isEmpty);
      voice.dispose();
    });

    testWidgets('the merge on the voice screen needs a tap on Confirm merge',
        (tester) async {
      final speech = FakeSpeech();
      final voice = await tester.runAsync(() => buildController(
          speech: speech,
          transport: FakeVoiceTransport(),
          preferences: MemoryVoicePreferences({'anum.voice.wake': 'off'})));
      final api = FakeDeveloperApi(
          requests: [requestJson('dev_1', status: 'waiting_approval', pr: 42)]);
      final assist = await _assist(api, 'voice');
      voice!.developer = assist;
      await _pump(tester, VoiceScreen(controller: voice),
          size: const Size(320, 568), textScale: 2);
      await tester.runAsync(assist.poll);
      await tester.runAsync(() => voice.ask('approve'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      final confirm = find.byKey(const Key('developer-confirm-merge'));
      await tester.scrollUntilVisible(find.text(exampleHash), 200,
          scrollable: find.byType(Scrollable).first);
      expect(find.text(exampleHash), findsOneWidget);
      expect(api.calls('POST', '/approve'), isEmpty);
      await tester.ensureVisible(confirm);
      await tester.pumpAndSettle();
      await tester.tap(confirm);
      await tester.pumpAndSettle();
      expect(api.calls('POST', '/approve').single.body,
          {'payload_hash': exampleHash});
      expect(find.text('Merged pull request #42.'), findsOneWidget);
      expect(DevStatus.parse('merged'), DevStatus.merged);
      await tester.pumpWidget(const SizedBox());
    });
  });
}
