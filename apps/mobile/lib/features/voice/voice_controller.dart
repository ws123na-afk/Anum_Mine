import 'package:flutter/foundation.dart';

import 'speech_service.dart';
import 'voice_models.dart';
import 'voice_repository.dart';

class VoiceController extends ChangeNotifier {
  VoiceController({required this.repository, required this.speech});
  final VoiceRepository repository;
  final SpeechService speech;

  VoicePhase phase = VoicePhase.idle;
  VoiceRetention retention = VoiceRetention.session;
  String locale = 'en-US';
  String transcript = '';
  String? message;
  VoiceSession? session;
  VoiceCommand? command;
  int _sequence = 0;

  bool get canSubmit => phase == VoicePhase.review && transcript.trim().isNotEmpty;

  void setTranscript(String value) {
    transcript = value;
    if (value.trim().isNotEmpty && phase != VoicePhase.listening) phase = VoicePhase.review;
    notifyListeners();
  }

  void setRetention(VoiceRetention value) { retention = value; notifyListeners(); }
  void setLocale(String value) { locale = value; notifyListeners(); }

  Future<void> start() async {
    message = null;
    phase = VoicePhase.requestingPermission;
    notifyListeners();
    try {
      final available = await speech.initialize(onError: _speechError);
      if (!available) {
        phase = VoicePhase.permissionDenied;
        message = 'Microphone or speech recognition access is unavailable.';
        notifyListeners();
        return;
      }
      session ??= await repository.createSession(locale: locale, retention: retention);
      phase = VoicePhase.listening;
      notifyListeners();
      await speech.listen(locale: locale, onResult: (text, finalResult) {
        transcript = text;
        if (finalResult) phase = VoicePhase.review;
        notifyListeners();
      });
    } on Object catch (error) { _fail(error); }
  }

  Future<void> stop() async {
    await speech.stop();
    phase = transcript.trim().isEmpty ? VoicePhase.idle : VoicePhase.review;
    notifyListeners();
  }

  Future<void> discard() async {
    await speech.cancel();
    if (session != null) await repository.cancel(session!.id);
    session = null; command = null; transcript = ''; message = null; phase = VoicePhase.idle;
    notifyListeners();
  }

  Future<void> submit() async {
    if (!canSubmit) return;
    phase = VoicePhase.submitting;
    notifyListeners();
    try {
      session ??= await repository.createSession(locale: locale, retention: retention);
      final segment = await repository.appendFinalTranscript(session!.id, transcript.trim(), _sequence++);
      phase = VoicePhase.running;
      notifyListeners();
      command = await repository.submitAndRun(session!.id, segment.id, _title(transcript));
      await repository.complete(session!.id);
      phase = VoicePhase.completed;
      notifyListeners();
    } on Object catch (error) { _fail(error); }
  }

  Future<void> speakConfirmation() async {
    final value = command;
    if (value != null) await speech.speak('${value.title}. Status ${value.status}.', locale);
  }

  void _speechError(String value) { message = value; phase = VoicePhase.error; notifyListeners(); }
  void _fail(Object error) { message = error.toString(); phase = VoicePhase.error; notifyListeners(); }
  String _title(String value) { final clean=value.trim(); return clean.length<=80?clean:'${clean.substring(0,77)}...'; }
}
