import 'package:anum_mobile/features/voice/voice_controller.dart';
import 'package:anum_mobile/features/voice/voice_preferences.dart';
import 'package:anum_mobile/features/voice/voice_screen.dart';
import 'package:anum_mobile/features/voice/speech_service.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'voice_fakes.dart';

Future<void> _pumpScreen(
  WidgetTester tester,
  VoiceController controller, {
  VoidCallback? onOpenApprovals,
}) async {
  tester.view.physicalSize = const Size(990, 2400);
  tester.view.devicePixelRatio = 2.75;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(context).copyWith(disableAnimations: true),
      child: child!,
    ),
    home: Scaffold(
      body:
          VoiceScreen(controller: controller, onOpenApprovals: onOpenApprovals),
    ),
  ));
  await tester.pumpAndSettle();
}

Future<void> _type(WidgetTester tester, String text) async {
  await tester.enterText(find.byType(TextField), text);
  await tester.pump();
  await tester.tap(find.text('Send'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('shows replies, approvals and the confirm flow', (tester) async {
    final speech = FakeSpeech();
    final transport = FakeVoiceTransport();
    final controller = await tester.runAsync(() => buildController(
        speech: speech,
        transport: transport,
        preferences: MemoryVoicePreferences({'anum.voice.wake': 'off'})));
    var opened = 0;
    await _pumpScreen(tester, controller!, onOpenApprovals: () => opened++);

    expect(find.text('Anum'), findsOneWidget);
    expect(find.text('Try saying'), findsOneWidget);
    expect(find.text('Wake off'), findsOneWidget);

    await _type(tester, 'approve the deploy');
    expect(find.text('approve the deploy'), findsOneWidget);
    expect(find.text('On screen only'), findsOneWidget);
    expect(find.text('2 approvals waiting for you'), findsOneWidget);
    await tester.ensureVisible(find.text('Open approvals'));
    await tester.tap(find.text('Open approvals'));
    expect(opened, 1);
    expect(transport.to('/commands'), isEmpty);

    await _type(tester, 'Create a task: draft the weekly report');
    expect(find.text('Needs your OK'), findsOneWidget);
    expect(find.text('Review command'), findsOneWidget);
    expect(transport.to('/commands'), isEmpty);
    await tester.ensureVisible(find.text('Create task'));
    await tester.tap(find.text('Create task'));
    await tester.pumpAndSettle();
    expect(transport.to('/commands'), hasLength(1));
    expect(find.text('Task created'), findsOneWidget);
    expect(find.text('Task created: draft the weekly report'), findsOneWidget);

    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('denied microphone shows the settings recovery', (tester) async {
    final speech = FakeSpeech()..availability = SpeechAvailability.denied;
    final transport = FakeVoiceTransport();
    final controller = await tester
        .runAsync(() => buildController(speech: speech, transport: transport));
    await _pumpScreen(tester, controller!);
    expect(find.text('Open app settings'), findsOneWidget);
    await tester.tap(find.text('Open app settings'));
    expect(speech.settingsOpened, 1);
    expect(transport.requests, isEmpty);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('settings rename the assistant', (tester) async {
    final preferences = MemoryVoicePreferences({'anum.voice.wake': 'off'});
    final controller = await tester.runAsync(() => buildController(
        speech: FakeSpeech(),
        transport: FakeVoiceTransport(),
        preferences: preferences));
    await _pumpScreen(tester, controller!);
    await tester.tap(find.byTooltip('Voice settings'));
    await tester.pumpAndSettle();
    expect(find.text('Arabic (Saudi Arabia)'), findsOneWidget);
    await tester.enterText(find.widgetWithText(TextField, 'Anum'), 'Layla');
    await tester.tap(find.byTooltip('Save name'));
    await tester.pumpAndSettle();
    expect(controller.assistantName, 'Layla');
    expect(preferences.values['anum.voice.name'], 'Layla');
    await tester.pumpWidget(const SizedBox());
  });
}
