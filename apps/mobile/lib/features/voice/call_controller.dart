import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/sse.dart';
import 'call_models.dart';
import 'sentences.dart';
import 'speech_service.dart';
import 'voice_models.dart';
import 'voice_repository.dart';

/// A live voice call with the assistant (docs/voice.md#call-mode).
///
/// Device recognition keeps listening for the whole call, also while a reply
/// is spoken: a loud, sustained input level or words that are not the reply's
/// own echo stop the reply at once (barge-in) and the caller's words become
/// the next turn. Replies stream and are spoken sentence by sentence. Actions
/// asked for by voice are only proposed; approvals never happen by voice.
class CallController extends ChangeNotifier {
  CallController({
    required this.repository,
    required this.speech,
    DateTime Function()? clock,
    this.bargeInLevel = 8.0,
    this.bargeInSamples = 3,
  }) : _clock = clock ?? DateTime.now;

  final VoiceRepository repository;
  final SpeechService speech;
  final DateTime Function() _clock;

  /// Input level (the recognizer's scale, about -2 to 10 dB) that counts as
  /// the caller talking over a reply when held for [bargeInSamples] readings.
  final double bargeInLevel;
  final int bargeInSamples;

  CallPhase phase = CallPhase.idle;
  CallProblem? problem;
  String? problemDetail;
  CallCapabilities capabilities = CallCapabilities.deviceOnly;
  String locale = 'en-US';
  String name = 'Anum';
  bool muted = false;
  bool speakerOn = true;
  bool speechAvailable = true;
  DateTime? startedAt;
  DateTime? endedAt;
  String caption = '';
  List<VoiceTurn> turns = const [];
  int pendingApprovals = 0;
  int bargeIns = 0;
  Duration? lastLatency;
  CallSummary? summary;

  bool get isArabic => locale.toLowerCase().startsWith('ar');
  bool get active =>
      phase != CallPhase.idle &&
      phase != CallPhase.ending &&
      phase != CallPhase.ended;
  bool get hasConversation => turns.any((turn) => turn.text.trim().isNotEmpty);
  Duration get elapsed => startedAt == null
      ? Duration.zero
      : (endedAt ?? _clock()).difference(startedAt!);

  String? _sessionId;
  int _sequence = 0;
  int _turnCounter = 0;
  int _loud = 0;
  int _replyToken = 0;
  bool _listening = false;
  bool _disposed = false;
  String _speaking = '';
  DateTime? _turnEnded;
  StreamSubscription<SseEvent>? _reply;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  /// Opens the session and starts listening. Problems are reported, not thrown.
  Future<void> start({
    required String locale,
    required String name,
    VoiceRetention retention = VoiceRetention.session,
  }) async {
    if (phase != CallPhase.idle) return;
    this.locale = locale;
    this.name = name;
    problem = null;
    phase = CallPhase.connecting;
    _notify();
    final availability = await speech.initialize();
    if (availability == SpeechAvailability.denied) {
      phase = CallPhase.idle;
      problem = CallProblem.micDenied;
      _notify();
      return;
    }
    speechAvailable = availability == SpeechAvailability.ready;
    try {
      capabilities = await repository.capabilities();
    } on Object {
      capabilities = CallCapabilities.deviceOnly;
    }
    try {
      final session = await repository.createSession(
          locale: locale, retention: retention, assistantName: name);
      _sessionId = session.id;
    } on Object catch (error) {
      phase = CallPhase.idle;
      _report(error);
      return;
    }
    startedAt = _clock();
    phase = CallPhase.listening;
    if (!speechAvailable) problem = CallProblem.speechUnavailable;
    _notify();
    _listen();
  }

  void _listen() {
    if (!active || muted || !speechAvailable || _listening) return;
    _listening = true;
    unawaited(speech.listen(
      locale: locale,
      onResult: _onResult,
      onLevel: _onLevel,
      onError: (message, permanent) {
        if (permanent && message.contains('permission')) {
          problem = CallProblem.micDenied;
          _notify();
        }
      },
      onDone: () {
        _listening = false;
        // Recognition ends after each pause: keep the call listening.
        if (active && !muted) {
          Timer(const Duration(milliseconds: 150), _listen);
        }
      },
      pauseFor: const Duration(milliseconds: 900),
      listenFor: const Duration(seconds: 60),
    ));
  }

  void _onLevel(double level) {
    if (phase != CallPhase.speaking) {
      _loud = 0;
      return;
    }
    _loud = level >= bargeInLevel ? _loud + 1 : 0;
    if (_loud >= bargeInSamples) interrupt(bargeIn: true);
  }

  void _onResult(String text, bool isFinal) {
    if (!active || muted) return;
    final clean = text.trim();
    if (clean.isEmpty) return;
    if (phase == CallPhase.speaking || phase == CallPhase.thinking) {
      final words = clean.split(RegExp(r'\s+')).length;
      if (soundsLikeEcho(clean, _speaking) && phase == CallPhase.speaking) {
        return;
      }
      if (words < 3 && !isFinal) return;
      interrupt(bargeIn: true);
    }
    caption = clean;
    _notify();
    if (isFinal) {
      _turnEnded = _clock();
      unawaited(submit(clean));
    }
  }

  /// Sends a question (spoken or typed), with an optional photo.
  Future<void> submit(String text,
      {Uint8List? photo, String photoType = 'image/jpeg'}) async {
    final sessionId = _sessionId;
    final clean = text.trim().isEmpty && photo != null
        ? (isArabic ? 'ماذا ترى؟' : 'What do you see?')
        : text.trim();
    if (clean.isEmpty || sessionId == null || !active) return;
    if (photo != null && !capabilities.visionAvailable) {
      problem = CallProblem.visionOff;
      _notify();
      return;
    }
    if (photo != null && photo.length > capabilities.maxFrameBytes) {
      problem = CallProblem.photoTooLarge;
      _notify();
      return;
    }
    _turnEnded ??= _clock();
    final token = ++_replyToken;
    await _reply?.cancel();
    _reply = null;
    final pendingId = 'call_assistant_${_turnCounter++}';
    caption = '';
    problem = null;
    phase = CallPhase.thinking;
    turns = [
      ...turns,
      VoiceTurn(
          id: 'call_you_${_turnCounter++}',
          speaker: VoiceSpeaker.you,
          text: photo == null
              ? clean
              : '$clean (${isArabic ? 'مع صورة' : 'with a photo'})'),
    ];
    _notify();
    try {
      final segment =
          await repository.appendFinalTranscript(sessionId, clean, _sequence++);
      if (token != _replyToken) return;
      final stream = photo == null
          ? repository.askStream(sessionId, segment.id)
          : repository.lookStream(
              sessionId, segment.id, base64Encode(photo), photoType);
      if (stream == null) {
        final result = await repository.ask(sessionId, segment.id);
        if (token != _replyToken) return;
        turns = [
          ...turns,
          VoiceTurn(
              id: result.assistantSegmentId,
              speaker: VoiceSpeaker.assistant,
              text: result.reply,
              result: result,
              sessionId: sessionId),
        ];
        pendingApprovals = result.workspace.pendingApprovals;
        await _speakAll(
            takeSentences(result.reply, isFinal: true).sentences, token);
      } else {
        await _streamReply(stream, pendingId, sessionId, token);
      }
      if (token == _replyToken && active) {
        phase = CallPhase.listening;
        _notify();
      }
    } on Object catch (error) {
      if (token != _replyToken) return;
      turns = turns
          .where((turn) => turn.id != pendingId || turn.text.isNotEmpty)
          .toList();
      if (active) phase = CallPhase.listening;
      _report(error);
    } finally {
      if (token == _replyToken) _turnEnded = null;
    }
  }

  Future<void> _streamReply(Stream<SseEvent> events, String pendingId,
      String sessionId, int token) async {
    final reply = StringBuffer();
    final queue = <String>[];
    var buffered = '';
    JsonMap? meta;
    String? failure;
    String? segmentId;
    Future<void>? speaking;
    final done = Completer<void>();

    void drain() {
      if (speaking != null || queue.isEmpty) return;
      speaking = () async {
        while (queue.isNotEmpty && token == _replyToken && speakerOn) {
          await _speakOne(queue.removeAt(0), token);
        }
        speaking = null;
      }();
    }

    void hear({required bool isFinal}) {
      final split = takeSentences(buffered, isFinal: isFinal);
      buffered = split.rest;
      queue.addAll(split.sentences);
      if (speakerOn) drain();
    }

    _reply = events.listen((event) {
      if (token != _replyToken) return;
      final data = event.fields;
      switch (event.event) {
        case 'meta':
          meta = data;
          pendingApprovals = WorkspaceFacts.fromJson(
                  JsonReader(data).optObject('workspace')?.json)
              .pendingApprovals;
          _notify();
        case 'delta':
          final text = data['text'];
          if (text is! String || text.isEmpty) return;
          if (!speakerOn) _markLatency();
          reply.write(text);
          buffered += text;
          _upsert(VoiceTurn(
              id: pendingId,
              speaker: VoiceSpeaker.assistant,
              text: reply.toString()));
          _notify();
          hear(isFinal: false);
        case 'done':
          final segment = data['assistant_segment'];
          segmentId = segment is Map && segment['id'] is String
              ? segment['id'] as String
              : null;
        case 'error':
          failure = data['message'] is String
              ? data['message'] as String
              : 'I could not answer.';
      }
    }, onError: (Object error) {
      if (!done.isCompleted) done.completeError(error);
    }, onDone: () {
      if (!done.isCompleted) done.complete();
    }, cancelOnError: true);
    await done.future;
    if (token != _replyToken) return;
    if (failure != null && reply.isEmpty) {
      throw ApiException(503, failure!, code: 'model_unavailable');
    }
    final metaJson = meta;
    if (metaJson != null) {
      final result = VoiceAskResult.fromJson({
        ...metaJson,
        'reply': reply.toString(),
        if (segmentId != null) 'assistant_segment': {'id': segmentId},
      });
      _upsert(VoiceTurn(
          id: pendingId,
          speaker: VoiceSpeaker.assistant,
          text: reply.toString(),
          result: result,
          sessionId: sessionId));
      _notify();
    }
    hear(isFinal: true);
    while (speaking != null) {
      await speaking;
    }
  }

  void _upsert(VoiceTurn turn) {
    final index = turns.indexWhere((item) => item.id == turn.id);
    turns = index < 0
        ? [...turns, turn]
        : [...turns.sublist(0, index), turn, ...turns.sublist(index + 1)];
  }

  void _markLatency() {
    final ended = _turnEnded;
    if (ended == null) return;
    lastLatency = _clock().difference(ended);
    _turnEnded = null;
  }

  Future<void> _speakAll(List<String> sentences, int token) async {
    for (final sentence in sentences) {
      if (token != _replyToken || !speakerOn) return;
      await _speakOne(sentence, token);
    }
  }

  Future<void> _speakOne(String sentence, int token) async {
    if (token != _replyToken || !active) return;
    phase = CallPhase.speaking;
    _speaking = sentence;
    _markLatency();
    _notify();
    try {
      // Some engines never report the end of speech; never get stuck.
      await speech.speak(sentence, locale).timeout(
          Duration(milliseconds: math.min(30000, 4000 + sentence.length * 80)));
    } on Object {
      // Speaking is best effort; the reply is on screen.
    }
    _speaking = '';
  }

  /// Stops the reply: the caller talked over it, or tapped.
  void interrupt({bool bargeIn = false}) {
    if (phase != CallPhase.speaking && phase != CallPhase.thinking) return;
    _replyToken++;
    unawaited(_reply?.cancel());
    _reply = null;
    unawaited(speech.stopSpeaking());
    _speaking = '';
    _loud = 0;
    if (bargeIn) bargeIns++;
    phase = CallPhase.listening;
    _notify();
  }

  void setMuted(bool value) {
    if (value == muted) return;
    muted = value;
    if (muted) {
      _listening = false;
      unawaited(speech.cancel());
      caption = '';
    }
    _notify();
    if (!muted) _listen();
  }

  void setSpeaker(bool value) {
    speakerOn = value;
    if (!value) unawaited(speech.stopSpeaking());
    _notify();
  }

  Future<String?> confirmTask(VoiceTurn turn) async {
    final proposal = turn.result?.proposedTask;
    final sessionId = _sessionId;
    if (proposal == null || sessionId == null) return null;
    try {
      final segment = await repository.appendFinalTranscript(
          sessionId, proposal, _sequence++);
      final created = await repository.createTask(sessionId, segment.id);
      _upsert(turn.copyWith(
          resolution: TurnResolution.created, createdTitle: created.title));
      _notify();
      return created.title;
    } on Object catch (error) {
      _report(error);
      return null;
    }
  }

  void dismiss(VoiceTurn turn) {
    _upsert(turn.copyWith(resolution: TurnResolution.dismissed));
    _notify();
  }

  void report(CallProblem value) {
    problem = value;
    problemDetail = null;
    _notify();
  }

  void clearProblem() {
    problem = null;
    problemDetail = null;
    _notify();
  }

  /// Hangs up: listening, speaking and the reply stop at once. Then [finish].
  void hangUp() {
    if (!active) return;
    _replyToken++;
    unawaited(_reply?.cancel());
    _reply = null;
    unawaited(speech.cancel());
    unawaited(speech.stopSpeaking());
    _listening = false;
    endedAt = _clock();
    caption = '';
    phase = _sessionId == null ? CallPhase.ended : CallPhase.ending;
    _notify();
  }

  /// After [hangUp]: keep a summary (as a conversation) or nothing, then
  /// close the session. False when saving failed and the user should choose.
  Future<bool> finish({required bool save}) async {
    final sessionId = _sessionId;
    if (phase != CallPhase.ending || sessionId == null) {
      return phase == CallPhase.ended;
    }
    if (save && hasConversation) {
      try {
        summary = await repository.summarize(sessionId,
            durationSeconds: elapsed.inSeconds, save: true);
      } on Object catch (error) {
        problem = CallProblem.summary;
        problemDetail = error is ApiException ? error.message : null;
        _notify();
        return false;
      }
    }
    try {
      await repository.complete(sessionId);
    } on Object {
      // The session is closed by retention either way.
    }
    _sessionId = null;
    problem = null;
    phase = CallPhase.ended;
    _notify();
    return true;
  }

  void _report(Object error) {
    problemDetail = null;
    if (error is ApiException) {
      problemDetail = error.message;
      problem = error.isBudgetExceeded
          ? CallProblem.budget
          : error.statusCode == 429
              ? CallProblem.limit
              : error.statusCode == 403
                  ? CallProblem.forbidden
                  : error.statusCode == 404 || error.statusCode == 409
                      ? CallProblem.session
                      : CallProblem.provider;
    } else if (error is EventStreamTimeoutException) {
      problem = CallProblem.offline;
    } else {
      problem = CallProblem.offline;
    }
    _notify();
  }

  @override
  void dispose() {
    _disposed = true;
    if (active) hangUp();
    final sessionId = _sessionId;
    // Leaving the screen without choosing keeps nothing: close the session.
    if (sessionId != null) {
      unawaited(repository.complete(sessionId).catchError((_) {}));
    }
    unawaited(_reply?.cancel());
    super.dispose();
  }
}
