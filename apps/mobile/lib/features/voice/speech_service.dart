import 'package:flutter_tts/flutter_tts.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:speech_to_text/speech_recognition_result.dart';
import 'package:speech_to_text/speech_to_text.dart';

abstract interface class SpeechService {
  Future<bool> initialize({required void Function(String message) onError});
  Future<void> listen({required String locale, required void Function(String text, bool finalResult) onResult});
  Future<void> stop();
  Future<void> cancel();
  Future<void> openSettings();
  Future<void> speak(String text, String locale);
}

class DeviceSpeechService implements SpeechService {
  DeviceSpeechService({SpeechToText? speech, FlutterTts? tts})
      : _speech = speech ?? SpeechToText(),
        _tts = tts ?? FlutterTts();

  final SpeechToText _speech;
  final FlutterTts _tts;

  @override
  Future<bool> initialize({required void Function(String message) onError}) async {
    final permission = await Permission.microphone.request();
    if (!permission.isGranted) return false;
    return _speech.initialize(
      onError: (error) => onError(error.errorMsg),
    );
  }

  @override
  Future<void> listen({required String locale, required void Function(String text, bool finalResult) onResult}) =>
      _speech.listen(
        localeId: locale,
        onResult: (SpeechRecognitionResult result) =>
            onResult(result.recognizedWords, result.finalResult),
      );

  @override
  Future<void> stop() => _speech.stop();

  @override
  Future<void> cancel() => _speech.cancel();

  @override
  Future<void> openSettings() async { await openAppSettings(); }

  @override
  Future<void> speak(String text, String locale) async {
    await _tts.stop();
    await _tts.setLanguage(locale);
    await _tts.setSpeechRate(0.45);
    await _tts.speak(text);
  }
}
