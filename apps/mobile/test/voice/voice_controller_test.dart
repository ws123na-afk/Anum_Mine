import 'package:anum_mobile/features/voice/speech_service.dart';
import 'package:anum_mobile/features/voice/voice_models.dart';
import 'package:anum_mobile/features/voice/voice_preferences.dart';
import 'package:flutter_test/flutter_test.dart';

import 'voice_fakes.dart';

void main() {
  late FakeSpeech speech;
  late FakeVoiceTransport transport;

  setUp(() {
    speech = FakeSpeech();
    transport = FakeVoiceTransport();
  });

  group('asking', () {
    test('sends the assistant name with the session and reads the reply',
        () async {
      final controller =
          await buildController(speech: speech, transport: transport);
      expect(await controller.setAssistantName('Layla'), isTrue);
      controller.setVisible(true);

      await controller.ask("What's your name?");

      final created = transport.to('/voice/sessions').single;
      expect(created.body!['assistant_name'], 'Layla');
      expect(created.body!['locale'], 'en-US');
      expect(created.body!['retention'], 'session');
      final ask = transport.to('/ask').single;
      expect(ask.path, '/api/v1/voice/sessions/voice_1/ask');
      expect(ask.body!['transcript_segment_id'], startsWith('segment_'));
      expect(controller.turns.map((turn) => turn.speaker),
          [VoiceSpeaker.you, VoiceSpeaker.assistant]);
      expect(controller.turns.last.result!.intent, 'identity');
      expect(speech.spoken, ["I'm your assistant here in ANUM."]);
      expect(controller.pendingApprovals, 2);
      expect(transport.to('/commands'), isEmpty);
    });

    test('a new name starts a new session so the server knows it', () async {
      final controller =
          await buildController(speech: speech, transport: transport);
      await controller.ask('status');
      await controller.ask('status again');
      expect(transport.to('/voice/sessions'), hasLength(1));
      await controller.setAssistantName('Noor');
      await controller.ask('status');
      expect(transport.to('/voice/sessions'), hasLength(2));
      expect(
          transport.to('/voice/sessions').last.body!['assistant_name'], 'Noor');
    });

    test('read replies listen once more for a follow-up without the name',
        () async {
      final controller =
          await buildController(speech: speech, transport: transport);
      await controller.load();
      controller.setVisible(true);
      await pumpEventQueue();
      expect(controller.wakeListening, isTrue);
      expect(speech.listens, hasLength(1));

      await controller.ask("what's running?");

      expect(controller.state, VoiceState.listening);
      expect(controller.followUp, isTrue);
      expect(speech.listens, hasLength(2));

      // Silence: hand back to listening for the name without a nag.
      speech.last.onDone();
      await pumpEventQueue();
      expect(controller.state, VoiceState.idle);
      expect(controller.notice, isNull);
      expect(controller.wakeListening, isTrue);
      expect(speech.listens, hasLength(3));
    });

    test('a follow-up that hears something is sent straight away', () async {
      final controller =
          await buildController(speech: speech, transport: transport);
      controller.setVisible(true);
      await pumpEventQueue();
      await controller.ask('status');
      speech.last.onResult('and what is my name', true);
      speech.last.onDone();
      await pumpEventQueue();
      expect(transport.to('/ask'), hasLength(2));
      expect(controller.turns.last.result!.intent, 'identity');
    });

    test('confirm tier creates a task only after the user taps Create task',
        () async {
      final controller =
          await buildController(speech: speech, transport: transport);
      controller.setVisible(true);
      await pumpEventQueue();

      await controller.ask('Create a task: draft the weekly report');

      final turn = controller.turns.last;
      expect(turn.result!.riskTier, VoiceRiskTier.confirm);
      expect(turn.result!.proposedTask, 'draft the weekly report');
      expect(turn.awaitingConfirmation, isTrue);
      expect(transport.to('/commands'), isEmpty);
      // No follow-up capture: the next step is a tap, not more speech.
      expect(controller.state, VoiceState.idle);
      expect(controller.followUp, isFalse);

      await controller.confirmTask(turn);

      final command = transport.to('/commands').single;
      expect(command.body!['title'], 'draft the weekly report');
      expect(command.body!['transcript_segment_id'], startsWith('segment_'));
      expect(controller.turns.last.resolution, TurnResolution.created);
      expect(controller.notice, contains('Task created'));

      // A second tap cannot create a duplicate.
      await controller.confirmTask(controller.turns.last);
      expect(transport.to('/commands'), hasLength(1));
    });

    test('Not now dismisses a proposal without creating anything', () async {
      final controller =
          await buildController(speech: speech, transport: transport);
      await controller.ask('Create a task: book the venue');
      controller.dismiss(controller.turns.last);
      await controller.confirmTask(controller.turns.last);
      expect(controller.turns.last.resolution, TurnResolution.dismissed);
      expect(transport.to('/commands'), isEmpty);
    });

    test('visual-only requests are never acted on by voice', () async {
      final controller =
          await buildController(speech: speech, transport: transport);
      await controller.ask('approve the deploy');
      final turn = controller.turns.last;
      expect(turn.result!.riskTier, VoiceRiskTier.visualOnly);
      expect(turn.awaitingConfirmation, isFalse);
      await controller.confirmTask(turn);
      expect(transport.to('/commands'), isEmpty);
      expect(transport.requests.where((r) => r.path.contains('approval')),
          isEmpty);
    });

    test('muted replies are shown but not spoken', () async {
      final controller =
          await buildController(speech: speech, transport: transport);
      await controller.toggleMute();
      await controller.ask('status');
      expect(speech.spoken, isEmpty);
      expect(controller.state, VoiceState.idle);
      expect(controller.turns, hasLength(2));
    });
  });

  group('wake by name', () {
    test('is on by default and listens only while visible', () async {
      final controller =
          await buildController(speech: speech, transport: transport);
      await controller.load();
      expect(controller.wakeEnabled, isTrue);
      expect(speech.listens, isEmpty);
      controller.setVisible(true);
      await pumpEventQueue();
      expect(speech.listens, hasLength(1));
      controller.setVisible(false);
      await pumpEventQueue();
      expect(controller.wakeListening, isFalse);
      expect(speech.cancels, greaterThan(0));
    });

    test('name plus a question sends the rest of the sentence', () async {
      final controller =
          await buildController(speech: speech, transport: transport);
      controller.setVisible(true);
      await pumpEventQueue();

      speech.last.onResult("Anum, what's running", false);
      await pumpEventQueue();
      expect(transport.requests, isEmpty, reason: 'partials are ignored');

      speech.last.onResult("Anum, what's running", true);
      await pumpEventQueue();
      expect(
          transport.to('/transcript').single.body!['text'], "what's running");
      expect(transport.to('/ask'), hasLength(1));
    });

    test('the name alone starts a capture turn', () async {
      final controller =
          await buildController(speech: speech, transport: transport);
      controller.setVisible(true);
      await pumpEventQueue();
      speech.last.onResult('hey Anum', true);
      await pumpEventQueue();
      expect(controller.state, VoiceState.listening);
      expect(controller.followUp, isFalse);
      expect(speech.listens, hasLength(2));
      expect(transport.requests, isEmpty);
    });

    test('other speech is ignored and listening restarts when it ends',
        () async {
      final controller =
          await buildController(speech: speech, transport: transport);
      controller.setVisible(true);
      await pumpEventQueue();
      speech.last.onResult('we should ask Sam about it', true);
      speech.last.onDone();
      await Future<void>.delayed(const Duration(milliseconds: 5));
      await pumpEventQueue();
      expect(transport.requests, isEmpty);
      expect(controller.wakeListening, isTrue);
      expect(speech.listens, hasLength(2));
    });

    test('the header toggle turns it off and the choice is saved', () async {
      final preferences = MemoryVoicePreferences();
      final controller = await buildController(
          speech: speech, transport: transport, preferences: preferences);
      controller.setVisible(true);
      await pumpEventQueue();
      await controller.toggleWake();
      expect(controller.wakeEnabled, isFalse);
      expect(controller.wakeListening, isFalse);
      expect(preferences.values['anum.voice.wake'], 'off');
      speech.listens.first.onResult('Anum what is running', true);
      await pumpEventQueue();
      expect(transport.requests, isEmpty, reason: 'stale listener is ignored');

      final restored = await buildController(
          speech: FakeSpeech(), transport: transport, preferences: preferences);
      await restored.load();
      expect(restored.wakeEnabled, isFalse);
    });

    test('name and language are restored from preferences', () async {
      final preferences = MemoryVoicePreferences(
          {'anum.voice.name': 'ليلى', 'anum.voice.locale': 'ar-SA'});
      final controller = await buildController(
          speech: speech, transport: transport, preferences: preferences);
      await controller.load();
      expect(controller.assistantName, 'ليلى');
      expect(controller.locale, 'ar-SA');
      controller.setVisible(true);
      await pumpEventQueue();
      expect(speech.last.locale, 'ar-SA');
      speech.last.onResult('يا ليلى ما حالة مهامي؟', true);
      await pumpEventQueue();
      expect(
          transport.to('/transcript').single.body!['text'], 'ما حالة مهامي؟');
      expect(transport.to('/voice/sessions').single.body!['locale'], 'ar-SA');
    });
  });

  group('tap to talk', () {
    test('a pause ends the turn and sends what was heard', () async {
      final controller = await buildController(
          speech: speech,
          transport: transport,
          preferences: MemoryVoicePreferences({'anum.voice.wake': 'off'}));
      await controller.load();
      controller.setVisible(true);
      await controller.tapOrb();
      expect(controller.state, VoiceState.listening);
      speech.last.onLevel?.call(-2);
      speech.last.onLevel?.call(8);
      expect(controller.level.value, 1);
      speech.last.onResult('what is running', false);
      expect(controller.heard, 'what is running');
      speech.last.onDone();
      await pumpEventQueue();
      expect(
          transport.to('/transcript').single.body!['text'], 'what is running');
      expect(speech.spoken, hasLength(1));
      expect(controller.state, VoiceState.idle,
          reason: 'wake is off, so no follow-up');
      expect(controller.level.value, 0);
    });

    test('silence asks the user to try again', () async {
      final controller = await buildController(
          speech: speech,
          transport: transport,
          preferences: MemoryVoicePreferences({'anum.voice.wake': 'off'}));
      await controller.load();
      await controller.tapOrb();
      speech.last.onDone();
      expect(controller.state, VoiceState.idle);
      expect(controller.notice, contains("didn't catch"));
      expect(transport.requests, isEmpty);
    });

    test('tapping while listening stops and sends', () async {
      final controller = await buildController(
          speech: speech,
          transport: transport,
          preferences: MemoryVoicePreferences({'anum.voice.wake': 'off'}));
      await controller.load();
      await controller.tapOrb();
      speech.last.onResult('status please', false);
      await controller.tapOrb();
      await pumpEventQueue();
      expect(speech.stops, 1);
      expect(transport.to('/ask'), hasLength(1));
    });
  });

  group('permissions', () {
    test('permission denial never creates a voice session', () async {
      speech.availability = SpeechAvailability.denied;
      final controller =
          await buildController(speech: speech, transport: transport);
      await controller.load();
      controller.setVisible(true);
      await pumpEventQueue();
      expect(controller.micBlocked, isTrue);
      expect(controller.wakeListening, isFalse);

      await controller.tapOrb();
      expect(controller.micBlocked, isTrue);
      expect(controller.state, VoiceState.idle);
      expect(speech.listens, isEmpty);
      expect(transport.requests, isEmpty);

      await controller.openSettings();
      expect(speech.settingsOpened, 1);
    });

    test('typing still works without the microphone', () async {
      speech.availability = SpeechAvailability.denied;
      final controller =
          await buildController(speech: speech, transport: transport);
      controller.setVisible(true);
      await pumpEventQueue();
      await controller.ask('status');
      expect(transport.to('/ask'), hasLength(1));
      expect(controller.state, VoiceState.idle);
    });

    test('a permission error while listening shows the recovery', () async {
      final controller =
          await buildController(speech: speech, transport: transport);
      controller.setVisible(true);
      await pumpEventQueue();
      speech.last.onError?.call('error_permission', true);
      speech.last.onDone();
      await Future<void>.delayed(const Duration(milliseconds: 5));
      expect(controller.micBlocked, isTrue);
      expect(speech.listens, hasLength(1), reason: 'no restart loop');
    });
  });

  test('clearing the conversation discards the server transcript', () async {
    final controller =
        await buildController(speech: speech, transport: transport);
    await controller.ask('status');
    await controller.clearConversation();
    expect(controller.turns, isEmpty);
    expect(controller.session, isNull);
    expect(transport.requests.last.method, 'DELETE');
  });
}
