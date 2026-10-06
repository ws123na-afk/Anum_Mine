import 'dart:async';

import 'package:flutter_tts/flutter_tts.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:speech_to_text/speech_recognition_error.dart';
import 'package:speech_to_text/speech_recognition_result.dart';
import 'package:speech_to_text/speech_to_text.dart';

enum SpeechAvailability { ready, denied, unavailable }

typedef SpeechResultCallback = void Function(String text, bool isFinal);
typedef SpeechErrorCallback = void Function(String message, bool permanent);

/// Device speech in and out. Kept behind an interface so the controller can
/// be tested with a fake.
abstract interface class SpeechService {
  /// Requests microphone (and speech) permission and prepares recognition.
  Future<SpeechAvailability> initialize();

  /// Starts one recognition session. [onDone] is called exactly once when
  /// the session ends on its own (pause, timeout, error or [stop]).
  Future<void> listen({
    required String locale,
    required SpeechResultCallback onResult,
    required void Function() onDone,
    void Function(double level)? onLevel,
    SpeechErrorCallback? onError,
    Duration pauseFor,
    Duration listenFor,
    List<String> phrases,
  });

  /// Ends listening and lets the recognizer deliver its final result.
  Future<void> stop();

  /// Ends listening and drops anything heard.
  Future<void> cancel();

  Future<void> openSettings();

  /// Speaks [text] and completes when speaking has finished.
  Future<void> speak(String text, String locale);

  Future<void> stopSpeaking();
}

class _ListenSession {
  _ListenSession(this.onResult, this.onDone, this.onLevel, this.onError);
  final SpeechResultCallback onResult;
  final void Function() onDone;
  final void Function(double level)? onLevel;
  final SpeechErrorCallback? onError;
  bool started = false;
  bool finished = false;

  void finish() {
    if (finished) return;
    finished = true;
    onDone();
  }
}

class DeviceSpeechService implements SpeechService {
  DeviceSpeechService({SpeechToText? speech, FlutterTts? tts})
      : _speech = speech ?? SpeechToText(),
        _tts = tts ?? FlutterTts();

  final SpeechToText _speech;
  final FlutterTts _tts;
  bool _initialized = false;
  _ListenSession? _current;
  String? _voiceLocale;

  @override
  Future<SpeechAvailability> initialize() async {
    final permission = await Permission.microphone.request();
    if (!permission.isGranted) return SpeechAvailability.denied;
    if (!_initialized) {
      _initialized = await _speech.initialize(
        onError: _onError,
        onStatus: _onStatus,
      );
    }
    if (_initialized) return SpeechAvailability.ready;
    return await _speech.hasPermission
        ? SpeechAvailability.unavailable
        : SpeechAvailability.denied;
  }

  void _onStatus(String status) {
    final session = _current;
    if (session == null) return;
    if (status == SpeechToText.listeningStatus) session.started = true;
    // A late "done" from a session we already replaced must not end the new
    // one, so only finish sessions that reported they had started.
    if (status == SpeechToText.doneStatus && session.started) {
      _current = null;
      session.finish();
    }
  }

  void _onError(SpeechRecognitionError error) {
    final session = _current;
    if (session == null) return;
    _current = null;
    session.onError?.call(error.errorMsg, error.permanent);
    session.finish();
  }

  @override
  Future<void> listen({
    required String locale,
    required SpeechResultCallback onResult,
    required void Function() onDone,
    void Function(double level)? onLevel,
    SpeechErrorCallback? onError,
    Duration pauseFor = const Duration(seconds: 2),
    Duration listenFor = const Duration(seconds: 30),
    List<String> phrases = const [],
  }) async {
    final session = _ListenSession(onResult, onDone, onLevel, onError);
    _current = session;
    try {
      await _speech.listen(
        onResult: (SpeechRecognitionResult result) {
          if (!session.finished) {
            session.onResult(result.recognizedWords, result.finalResult);
          }
        },
        onSoundLevelChange: (level) => session.onLevel?.call(level),
        listenOptions: SpeechListenOptions(
          localeId: locale,
          partialResults: true,
          cancelOnError: true,
          listenMode: ListenMode.dictation,
          pauseFor: pauseFor,
          listenFor: listenFor,
          contextualPhrases: phrases.isEmpty ? null : phrases,
        ),
      );
      // Some engines never send a "listening" status; trust a running engine.
      if (_speech.isListening) session.started = true;
    } on Object catch (error) {
      if (_current == session) _current = null;
      session.onError?.call(error.toString(), false);
      session.finish();
    }
  }

  @override
  Future<void> stop() => _speech.stop();

  @override
  Future<void> cancel() async {
    _current = null;
    await _speech.cancel();
  }

  @override
  Future<void> openSettings() async {
    await openAppSettings();
  }

  @override
  Future<void> speak(String text, String locale) async {
    await _tts.stop();
    await _tts.awaitSpeakCompletion(true);
    if (_voiceLocale != locale) {
      _voiceLocale = locale;
      await _tts.setLanguage(locale);
      await _chooseNaturalVoice(locale);
    }
    // 0.5 is a normal conversational pace on Android and iOS engines.
    await _tts.setSpeechRate(0.5);
    await _tts.setPitch(1.0);
    await _tts.speak(text);
  }

  Future<void> _chooseNaturalVoice(String locale) async {
    try {
      final raw = await _tts.getVoices;
      if (raw is! List) return;
      final voices = [
        for (final item in raw)
          if (item is Map)
            {
              for (final entry in item.entries)
                '${entry.key}': '${entry.value}',
            }
      ];
      final ranked = rankTtsVoices(voices, locale);
      if (ranked.isNotEmpty) await _tts.setVoice(ranked.first);
    } on Object {
      // Keep the engine's default voice for this language.
    }
  }

  @override
  Future<void> stopSpeaking() async {
    await _tts.stop();
  }
}

const _naturalHints = [
  'neural',
  'network',
  'enhanced',
  'premium',
  'natural',
  'wavenet',
  'studio',
];

int _qualityScore(String quality) {
  final value = quality.toLowerCase();
  if (value == 'very high' || value.contains('premium')) return 4;
  if (value == 'high' || value.contains('enhanced')) return 3;
  if (value == 'normal' || value == 'default') return 1;
  return 0;
}

String _normalizeLocale(String value) =>
    value.replaceAll('_', '-').toLowerCase();

/// Orders the engine's voices for [locale] so the most natural come first.
/// Voices for other languages are dropped.
List<Map<String, String>> rankTtsVoices(
    List<Map<String, String>> voices, String locale) {
  final wanted = _normalizeLocale(locale);
  final language = wanted.split('-').first;
  int score(Map<String, String> voice) {
    final name = (voice['name'] ?? '').toLowerCase();
    final voiceLocale = _normalizeLocale(voice['locale'] ?? '');
    var total = 0;
    if (voiceLocale == wanted) total += 5;
    if (_naturalHints.any(name.contains)) total += 4;
    total += _qualityScore(voice['quality'] ?? '');
    if (voice['network_required'] == '1') total += 1;
    if (name.contains('compact') || name.contains('legacy')) total -= 3;
    return total;
  }

  final matching = voices.where((voice) {
    final value = _normalizeLocale(voice['locale'] ?? '');
    return value == language || value.startsWith('$language-');
  }).toList();
  final scores = {for (final voice in matching) voice: score(voice)};
  matching.sort((a, b) => scores[b]!.compareTo(scores[a]!));
  return matching;
}
