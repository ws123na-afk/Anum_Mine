// Voice call mode (docs/voice.md#call-mode): the call loop with barge-in, the
// photo answers, the end-of-call summary, and the call screen at phone,
// tablet and desktop widths and at 200% text.
import 'dart:async';
import 'dart:typed_data';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/sse.dart';
import 'package:anum_mobile/features/voice/call_controller.dart';
import 'package:anum_mobile/features/voice/call_models.dart';
import 'package:anum_mobile/features/voice/call_screen.dart';
import 'package:anum_mobile/features/voice/speech_service.dart';
import 'package:anum_mobile/features/voice/voice_models.dart';
import 'package:anum_mobile/features/voice/voice_repository.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'voice_fakes.dart';

/// Speech that keeps "speaking" until stopped or finished by the test.
class _SlowSpeech extends FakeSpeech {
  Completer<void>? speaking;
  int stopped = 0;

  @override
  Future<void> speak(String text, String locale) {
    spoken.add(text);
    speaking = Completer<void>();
    return speaking!.future;
  }

  void finishSentence() {
    final current = speaking;
    speaking = null;
    if (current != null && !current.isCompleted) current.complete();
  }

  @override
  Future<void> stopSpeaking() async {
    stopped++;
    finishSentence();
  }
}

class _CallTransport extends FakeVoiceTransport {
  _CallTransport({this.vision = false});
  final bool vision;
  final summaries = <JsonMap>[];

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    final path = request.uri.path;
    if (path.endsWith('/voice/capabilities')) {
      requests.add(RecordedRequest(request.method, path, request.body));
      return ApiResponse(statusCode: 200, body: {
        'vision': {
          'available': vision,
          'model': vision ? 'claude-haiku-5-5' : null,
          'reason': vision ? null : 'Camera answers need a vision model.',
          'max_frame_bytes': 1000,
        },
        'latency_target_ms': {'first_audio': 1500},
      });
    }
    if (path.endsWith('/summary')) {
      requests.add(RecordedRequest(request.method, path, request.body));
      summaries.add(request.body!);
      return const ApiResponse(statusCode: 200, body: {
        'summary': '- Asked about the workspace.',
        'source': 'model',
        'conversation_id': 'conv_1',
      });
    }
    return super.send(request);
  }
}

class _Events implements EventStreamClient {
  final paths = <String>[];
  final bodies = <JsonMap>[];
  StreamController<SseEvent>? current;

  @override
  Stream<SseEvent> post(String path, JsonMap body) {
    paths.add(path);
    bodies.add(body);
    current = StreamController<SseEvent>();
    return current!.stream;
  }

  void reply(String text, {bool vision = false}) {
    final stream = current!;
    stream.add(SseEvent('meta', {
      'intent': 'question',
      'risk_tier': 'read',
      'proposed_task': null,
      'vision': vision,
      'workspace': {
        'tasks_total': 1,
        'running': 0,
        'waiting_approval': 0,
        'pending_approvals': 2
      },
    }));
    stream.add(SseEvent('delta', {'text': text}));
    stream.add(const SseEvent('done', {
      'assistant_segment': {'id': 'assistant_1'}
    }));
    unawaited(stream.close());
  }
}

Future<(CallController, _SlowSpeech, _CallTransport, _Events)> _call(
    {bool vision = false,
    SpeechAvailability availability = SpeechAvailability.ready}) async {
  final speech = _SlowSpeech()..availability = availability;
  final transport = _CallTransport(vision: vision);
  final events = _Events();
  final controller = CallController(
    repository: VoiceRepository(await signedInApi(transport), events: events),
    speech: speech,
  );
  return (controller, speech, transport, events);
}

Future<void> _settle() async {
  for (var i = 0; i < 10; i++) {
    await Future<void>.delayed(Duration.zero);
  }
}

void main() {
  test('formats the call timer and tells echo from new words', () {
    expect(formatCallTime(const Duration(seconds: 7)), '0:07');
    expect(formatCallTime(const Duration(minutes: 12, seconds: 34)), '12:34');
    expect(formatCallTime(const Duration(hours: 1, minutes: 2, seconds: 3)),
        '1:02:03');
    expect(soundsLikeEcho('the report is ready', 'The report is ready now.'),
        isTrue);
    expect(soundsLikeEcho('stop wait please now', 'The report is ready now.'),
        isFalse);
  });

  test('a spoken turn streams, is spoken, and talking over it stops it',
      () async {
    final (call, speech, transport, events) = await _call();
    await call.start(locale: 'en-US', name: 'Anum');
    expect(call.phase, CallPhase.listening);
    expect(speech.listens, hasLength(1));

    speech.last.onResult('what is running', true);
    await _settle();
    expect(events.paths.single, endsWith('/ask/stream'));
    events.reply('You have one task. Nothing is running right now.');
    await _settle();
    expect(call.phase, CallPhase.speaking);
    expect(speech.spoken, ['You have one task.']);
    expect(call.pendingApprovals, 2);

    // The reply's own echo does not interrupt; a loud voice does.
    speech.last.onResult('you have one task', false);
    expect(call.phase, CallPhase.speaking);
    for (var i = 0; i < 3; i++) {
      speech.last.onLevel!(9.5);
    }
    expect(call.phase, CallPhase.listening);
    expect(call.bargeIns, 1);
    expect(speech.stopped, 1);
    await _settle();
    expect(speech.spoken, ['You have one task.'],
        reason: 'the rest of the reply is not spoken after barge-in');
  });

  test('new words over the reply interrupt it and become the next question',
      () async {
    final (call, speech, transport, events) = await _call();
    await call.start(locale: 'en-US', name: 'Anum');
    speech.last.onResult('hello', true);
    await _settle();
    events.reply('Hi there, how can I help you today?');
    await _settle();
    expect(call.phase, CallPhase.speaking);
    speech.last.onResult('actually show me the approvals', true);
    await _settle();
    expect(call.bargeIns, 1);
    expect(events.paths, hasLength(2));
    expect(transport.to('/transcript').map((r) => r.body!['text']),
        ['hello', 'actually show me the approvals']);
  });

  test('mute stops listening; speaker off keeps replies as text only',
      () async {
    final (call, speech, _, events) = await _call();
    await call.start(locale: 'en-US', name: 'Anum');
    call.setMuted(true);
    expect(speech.cancels, 1);
    speech.last.onResult('ignored', true);
    await _settle();
    expect(events.paths, isEmpty);
    call.setMuted(false);
    call.setSpeaker(false);
    unawaited(call.submit('status'));
    await _settle();
    events.reply('All quiet. Nothing running.');
    await _settle();
    expect(speech.spoken, isEmpty);
    expect(call.turns.last.text, 'All quiet. Nothing running.');
    expect(call.lastLatency, isNotNull);
  });

  test('a photo goes to the vision endpoint only when a vision model exists',
      () async {
    final (call, _, _, events) = await _call(vision: true);
    await call.start(locale: 'ar-SA', name: 'Anum');
    unawaited(call.submit('', photo: Uint8List.fromList([0xff, 0xd8, 0xff])));
    await _settle();
    expect(events.paths.single, endsWith('/look/stream'));
    expect(events.bodies.single['image_base64'], '/9j/');
    expect(call.turns.first.text, contains('ماذا ترى؟'));

    final (blind, _, _, blindEvents) = await _call();
    await blind.start(locale: 'en-US', name: 'Anum');
    await blind.submit('', photo: Uint8List.fromList([1, 2, 3]));
    expect(blind.problem, CallProblem.visionOff);
    expect(blindEvents.paths, isEmpty);
    await blind.submit('', photo: Uint8List(2000));
    expect(blindEvents.paths, isEmpty);
  });

  test('a blocked microphone and a missing recognizer are explained', () async {
    final (denied, _, _, _) =
        await _call(availability: SpeechAvailability.denied);
    await denied.start(locale: 'en-US', name: 'Anum');
    expect(denied.phase, CallPhase.idle);
    expect(denied.problem, CallProblem.micDenied);

    final (typing, speech, _, _) =
        await _call(availability: SpeechAvailability.unavailable);
    await typing.start(locale: 'en-US', name: 'Anum');
    expect(typing.phase, CallPhase.listening);
    expect(typing.problem, CallProblem.speechUnavailable);
    expect(speech.listens, isEmpty);
  });

  test('a model failure is reported and leaves no empty reply', () async {
    final (call, _, _, events) = await _call();
    await call.start(locale: 'en-US', name: 'Anum');
    unawaited(call.submit('hello'));
    await _settle();
    events.current!
      ..add(const SseEvent('error', {'message': 'I could not answer.'}))
      ..close();
    await _settle();
    expect(call.problem, CallProblem.provider);
    expect(call.problemDetail, 'I could not answer.');
    expect(call.turns.map((t) => t.speaker), [VoiceSpeaker.you]);
  });

  test('hang up then save: summary first, then the session closes', () async {
    final (call, _, transport, events) = await _call();
    await call.start(locale: 'en-US', name: 'Anum');
    unawaited(call.submit('hello'));
    await _settle();
    events.reply('Hello.');
    await _settle();
    call.hangUp();
    expect(call.phase, CallPhase.ending);
    expect(await call.finish(save: true), isTrue);
    expect(transport.summaries.single['save'], isTrue);
    expect(call.summary!.conversationId, 'conv_1');
    expect(transport.to('/complete'), hasLength(1));
    expect(call.phase, CallPhase.ended);
  });

  group('call screen', () {
    Widget app(CallController call, {Locale locale = const Locale('en')}) =>
        MaterialApp(
          locale: locale,
          supportedLocales: const [Locale('en'), Locale('ar')],
          localizationsDelegates: const [
            AnumLocalizations.delegate,
            ...GlobalMaterialLocalizations.delegates,
          ],
          home: CallScreen(
              controller: call,
              locale: 'en-US',
              name: 'Anum',
              onOpenApprovals: () {}),
        );

    for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
      for (final scale in [1.0, 2.0]) {
        testWidgets('fits at ${width.toInt()} px wide, text x$scale',
            (tester) async {
          tester.view.physicalSize = Size(width, width < 700 ? 780 : 900);
          tester.view.devicePixelRatio = 1;
          addTearDown(tester.view.reset);
          tester.platformDispatcher.textScaleFactorTestValue = scale;
          addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
          final (call, _, _, _) = await _call();
          await tester.pumpWidget(app(call));
          await tester.pump();
          await tester.pump(const Duration(milliseconds: 50));
          expect(find.text('Listening'), findsOneWidget);
          expect(find.bySemanticsLabel('Mute microphone'), findsWidgets);
          expect(find.text('End call'), findsOneWidget);
          await tester.tap(find.bySemanticsLabel('Show transcript'));
          await tester.pump();
          expect(find.text('Transcript'), findsOneWidget);
          expect(tester.takeException(), isNull);
          call.dispose();
          await tester.pumpWidget(const SizedBox());
        });
      }
    }

    testWidgets('ending asks to save, then shows the summary', (tester) async {
      final (call, _, _, events) = await _call();
      await tester.pumpWidget(app(call));
      await tester.pump();
      await tester.pump();
      unawaited(call.submit('hello'));
      await tester.pump();
      events.reply('Hello.');
      await tester.pump();
      await tester.pump();
      await tester.tap(find.text('End call'));
      await tester.pump();
      expect(find.text('Save this call?'), findsOneWidget);
      await tester.tap(find.text('Save summary'));
      await tester.pump();
      await tester.pump();
      expect(find.text('- Asked about the workspace.'), findsOneWidget);
      call.dispose();
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('Arabic labels and right-to-left layout', (tester) async {
      final (call, _, _, _) = await _call();
      await tester.pumpWidget(app(call, locale: const Locale('ar')));
      await tester.pump();
      await tester.pump();
      expect(find.text('إنهاء المكالمة'), findsOneWidget);
      expect(find.text('يستمع'), findsOneWidget);
      expect(Directionality.of(tester.element(find.text('إنهاء المكالمة'))),
          TextDirection.rtl);
      call.dispose();
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('without a vision model, Video explains how to get one',
        (tester) async {
      final (call, _, _, _) = await _call();
      await tester.pumpWidget(app(call));
      await tester.pump();
      await tester.pump();
      await tester.tap(find.bySemanticsLabel('Show a photo'));
      await tester.pump();
      expect(find.textContaining('vision model'), findsOneWidget);
      call.dispose();
      await tester.pumpWidget(const SizedBox());
    });
  });
}
