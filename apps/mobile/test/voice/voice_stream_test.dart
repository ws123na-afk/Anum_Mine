// Voice speaks the reply sentence by sentence while it streams
// (`/voice/sessions/{id}/ask/stream`, docs/voice.md).
import 'dart:async';

import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/sse.dart';
import 'package:anum_mobile/features/voice/sentences.dart';
import 'package:anum_mobile/features/voice/voice_controller.dart';
import 'package:anum_mobile/features/voice/voice_models.dart';
import 'package:anum_mobile/features/voice/voice_preferences.dart';
import 'package:anum_mobile/features/voice/voice_repository.dart';
import 'package:flutter_test/flutter_test.dart';

import 'voice_fakes.dart';

class _VoiceEvents implements EventStreamClient {
  final paths = <String>[];
  final bodies = <JsonMap>[];
  late StreamController<SseEvent> current;

  @override
  Stream<SseEvent> post(String path, JsonMap body) {
    paths.add(path);
    bodies.add(body);
    current = StreamController<SseEvent>();
    return current.stream;
  }

  void emit(String event, Object? data) => current.add(SseEvent(event, data));
}

const _meta = {
  'intent': 'status',
  'risk_tier': 'read',
  'proposed_task': null,
  'workspace': {
    'tasks_total': 3,
    'running': 1,
    'waiting_approval': 1,
    'pending_approvals': 4
  },
};

void main() {
  group('takeSentences', () {
    test('splits complete sentences and keeps the unfinished tail', () {
      final split = takeSentences('You have three tasks running. One is wai');
      expect(split.sentences, ['You have three tasks running.']);
      expect(split.rest, 'One is wai');
    });

    test('merges very short fragments into the next sentence', () {
      final split = takeSentences('Hi. See e.g. the report from today. And',
          minLength: 12);
      expect(split.sentences, ['Hi. See e.g.', 'the report from today.']);
      expect(split.rest, 'And');
    });

    test('newlines, Arabic and ellipsis end sentences; final flushes', () {
      final split = takeSentences(
          'First line of the answer\nهل تريد إنشاء مهمة؟ Thinking about it… ok',
          isFinal: true);
      expect(split.sentences, [
        'First line of the answer',
        'هل تريد إنشاء مهمة؟',
        'Thinking about it…',
        'ok',
      ]);
      expect(split.rest, isEmpty);
    });
  });

  group('streamed voice replies', () {
    late FakeSpeech speech;
    late FakeVoiceTransport transport;
    late _VoiceEvents events;
    late VoiceController controller;

    setUp(() async {
      speech = FakeSpeech();
      transport = FakeVoiceTransport();
      events = _VoiceEvents();
      controller = VoiceController(
        repository:
            VoiceRepository(await signedInApi(transport), events: events),
        speech: speech,
        preferences: MemoryVoicePreferences(),
        restartDelay: Duration.zero,
      );
      controller.setVisible(true);
      await pumpEventQueue();
    });

    test('speaks the first sentence before the reply ends', () async {
      final asking = controller.ask('status');
      await pumpEventQueue();
      expect(events.paths.single, '/api/v1/voice/sessions/voice_1/ask/stream');
      expect(events.bodies.single['transcript_segment_id'],
          startsWith('segment_'));
      expect(transport.to('/ask'), isEmpty);

      events.emit('meta', _meta);
      events.emit('delta', {'text': 'You have three tasks running. One is'});
      await pumpEventQueue();
      expect(speech.spoken, ['You have three tasks running.']);
      expect(
          controller.turns.last.text, 'You have three tasks running. One is');
      expect(controller.pendingApprovals, 4);

      events.emit('delta', {'text': ' waiting for approval.'});
      events.emit('done', {
        'assistant_segment': {'id': 'segment_reply'}
      });
      await events.current.close();
      await asking;

      expect(speech.spoken, [
        'You have three tasks running.',
        'One is waiting for approval.',
      ]);
      final reply = controller.turns.last;
      expect(reply.id, 'segment_reply');
      expect(reply.result!.intent, 'status');
      expect(reply.result!.reply,
          'You have three tasks running. One is waiting for approval.');
      expect(controller.turns.map((t) => t.speaker),
          [VoiceSpeaker.you, VoiceSpeaker.assistant]);
      // A read reply listens once more for a follow-up, as before.
      expect(controller.state, VoiceState.listening);
      expect(controller.followUp, isTrue);
    });

    test('an error before any text leaves no empty bubble and says why',
        () async {
      final asking = controller.ask('status');
      await pumpEventQueue();
      events.emit('meta', _meta);
      events.emit('error', {
        'code': 'model_unavailable',
        'message': 'I could not answer. Try again in a moment.'
      });
      await events.current.close();
      await asking;
      expect(controller.turns.map((t) => t.speaker), [VoiceSpeaker.you]);
      expect(controller.notice, 'I could not answer. Try again in a moment.');
      expect(controller.state, VoiceState.idle);
      expect(speech.spoken, isEmpty);
    });

    test('muted: the reply streams on screen and nothing is spoken', () async {
      await controller.toggleMute();
      final asking = controller.ask('status');
      await pumpEventQueue();
      events.emit('meta', _meta);
      events.emit('delta', {'text': 'Nothing is running right now.'});
      events.emit('done', {'assistant_segment': null});
      await events.current.close();
      await asking;
      expect(speech.spoken, isEmpty);
      expect(controller.turns.last.text, 'Nothing is running right now.');
      expect(controller.state, VoiceState.idle);
    });

    test('a proposal waits for a tap, with no follow-up capture', () async {
      final asking = controller.ask('Create a task: send the report');
      await pumpEventQueue();
      events.emit('meta', {
        ..._meta,
        'intent': 'create_task',
        'risk_tier': 'confirm',
        'proposed_task': 'send the report',
      });
      events.emit('delta',
          {'text': 'Got it: "send the report". Tap Create task to set it up.'});
      events.emit('done', {
        'assistant_segment': {'id': 'segment_reply'}
      });
      await events.current.close();
      await asking;
      expect(controller.turns.last.awaitingConfirmation, isTrue);
      expect(controller.state, VoiceState.idle);
      expect(controller.followUp, isFalse);
      expect(transport.to('/commands'), isEmpty);
    });
  });
}
