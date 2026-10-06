import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'speech_service.dart';
import 'voice_models.dart';
import 'voice_preferences.dart';
import 'voice_repository.dart';
import 'wake_word.dart';

/// Conversational voice assistant.
///
/// Voice is untrusted input: spoken requests only reach the read-only `ask`
/// endpoint. A task is created only after the user taps "Create task", and
/// approvals, deletes and payments are always left to the visual flow.
class VoiceController extends ChangeNotifier {
  VoiceController({
    required this.repository,
    required this.speech,
    VoicePreferences? preferences,
    this.restartDelay = const Duration(milliseconds: 400),
  }) : preferences = preferences ?? SharedVoicePreferences();

  final VoiceRepository repository;
  final SpeechService speech;
  final VoicePreferences preferences;

  /// Pause between background listening sessions while waiting for the name.
  final Duration restartDelay;

  static const _kName = 'anum.voice.name';
  static const _kWake = 'anum.voice.wake';
  static const _kLocale = 'anum.voice.locale';
  static const _kMuted = 'anum.voice.muted';
  static const _kRetention = 'anum.voice.retention';
  static const _captureFor = Duration(seconds: 30);
  static const _followUpFor = Duration(seconds: 8);
  static const _wakeFor = Duration(seconds: 60);

  // Settings.
  String assistantName = defaultAssistantName;
  bool wakeEnabled = true;
  bool muted = false;
  String locale = 'en-US';
  VoiceRetention retention = VoiceRetention.session;

  // Live state.
  VoiceState state = VoiceState.idle;
  bool micBlocked = false;
  bool speechUnavailable = false;

  /// Listening in the background for the assistant's name.
  bool wakeListening = false;

  /// The current capture is a follow-up after a spoken reply.
  bool followUp = false;

  /// Words heard so far in the current capture.
  String heard = '';
  String? notice;
  int pendingApprovals = 0;
  List<VoiceTurn> turns = const [];
  VoiceSession? session;

  /// Microphone level, 0..1, for the orb. Separate so it does not rebuild
  /// the whole screen many times a second.
  final ValueNotifier<double> level = ValueNotifier(0);

  bool _visible = false;
  bool _loaded = false;
  bool _disposed = false;
  bool _returningFromSettings = false;
  int _listenToken = 0;
  int _speakToken = 0;
  int _turnCounter = 0;
  int _quickFailures = 0;
  DateTime? _wakeStartedAt;
  Timer? _restartTimer;
  Timer? _watchdog;
  String? _sessionLocale;
  String? _sessionName;
  final Map<String, int> _sequences = {};
  double _minLevel = double.infinity;
  double _maxLevel = double.negativeInfinity;

  bool get isArabic => locale.toLowerCase().startsWith('ar');
  bool get isVisible => _visible;

  /// Restores saved settings. Safe to call more than once.
  Future<void> load() async {
    if (_loaded) return;
    _loaded = true;
    final name = await preferences.read(_kName);
    final wake = await preferences.read(_kWake);
    final savedLocale = await preferences.read(_kLocale);
    final mute = await preferences.read(_kMuted);
    final keep = await preferences.read(_kRetention);
    if (_disposed) return;
    final cleanName = sanitizeAssistantName(name ?? '');
    if (cleanName.isNotEmpty) assistantName = cleanName;
    wakeEnabled = wake != 'off';
    muted = mute == 'on';
    retention = VoiceRetention.values
        .firstWhere((value) => value.apiValue == keep, orElse: () => retention);
    if (savedLocale == 'en-US' || savedLocale == 'ar-SA') {
      if (savedLocale != locale) await setLocale(savedLocale!);
    }
    if (!wakeEnabled && wakeListening) await _stopBackgroundListening();
    _notify();
    _maybeListenForWake();
  }

  /// Called by the screen when it is shown or hidden (tab switch, app
  /// paused). Background listening only happens while visible.
  void setVisible(bool value) {
    if (_disposed) return;
    if (value && _returningFromSettings) {
      _returningFromSettings = false;
      micBlocked = false;
    }
    if (_visible == value) {
      if (value) _maybeListenForWake();
      return;
    }
    _visible = value;
    if (value) {
      _maybeListenForWake();
    } else {
      unawaited(_stopEverything());
    }
  }

  // ---------------------------------------------------------------- input

  /// Orb tap: talk, stop talking, or stop the assistant speaking.
  Future<void> tapOrb() async {
    switch (state) {
      case VoiceState.listening:
        final token = _listenToken;
        await speech.stop();
        _finishCapture(token);
      case VoiceState.speaking:
        await _interruptSpeaking();
        state = VoiceState.idle;
        _notify();
        _maybeListenForWake();
      case VoiceState.thinking:
        return;
      case VoiceState.idle:
        await startCapture();
    }
  }

  /// Starts one turn of listening that ends on a pause and sends itself.
  Future<void> startCapture({bool followUp = false}) async {
    if (_disposed ||
        state == VoiceState.listening ||
        state == VoiceState.thinking) {
      return;
    }
    _invalidateListening();
    final token = _listenToken;
    if (state == VoiceState.speaking) await _interruptSpeaking();
    if (!followUp) micBlocked = false;
    if (!await _ensureSpeech()) {
      if (state == VoiceState.speaking) state = VoiceState.idle;
      _notify();
      return;
    }
    if (token != _listenToken) return;
    await speech.cancel();
    if (token != _listenToken || _disposed) return;
    this.followUp = followUp;
    heard = '';
    notice = null;
    _resetLevel();
    state = VoiceState.listening;
    _notify();
    final limit = followUp ? _followUpFor : _captureFor;
    _armWatchdog(limit + const Duration(seconds: 5), () {
      if (token == _listenToken) unawaited(_stopAndFinish(token));
    });
    await speech.listen(
      locale: locale,
      pauseFor: const Duration(seconds: 2),
      listenFor: limit,
      phrases: [assistantName],
      onResult: (text, _) {
        if (token != _listenToken) return;
        heard = text;
        _notify();
      },
      onLevel: (value) {
        if (token == _listenToken) _setLevel(value);
      },
      onError: (message, permanent) {
        if (token == _listenToken) _handleSpeechError(message);
      },
      onDone: () => _finishCapture(token),
    );
  }

  Future<void> _stopAndFinish(int token) async {
    await speech.stop();
    _finishCapture(token);
  }

  void _finishCapture(int token) {
    if (_disposed || token != _listenToken || state != VoiceState.listening) {
      return;
    }
    _invalidateListening();
    _resetLevel();
    final text = heard.trim();
    final wasFollowUp = followUp;
    heard = '';
    followUp = false;
    if (text.isNotEmpty) {
      unawaited(ask(text));
      return;
    }
    state = VoiceState.idle;
    // A silent follow-up window just hands back to listening for the name.
    if (!wasFollowUp && !micBlocked) {
      notice = isArabic
          ? 'لم أسمع شيئًا. حاول مرة أخرى.'
          : "I didn't catch that. Try again?";
    }
    _notify();
    _maybeListenForWake();
  }

  // ------------------------------------------------------------- asking

  /// Sends what the user said or typed to the assistant and speaks the reply.
  Future<void> ask(String text) async {
    final clean = text.trim();
    if (_disposed || clean.isEmpty || state == VoiceState.thinking) return;
    final wasListening = wakeListening || state == VoiceState.listening;
    _invalidateListening();
    if (wasListening) await speech.cancel();
    if (state == VoiceState.speaking) await _interruptSpeaking();
    state = VoiceState.thinking;
    notice = null;
    heard = '';
    followUp = false;
    _resetLevel();
    turns = [
      ...turns,
      VoiceTurn(
          id: 'you_${_turnCounter++}', speaker: VoiceSpeaker.you, text: clean),
    ];
    _notify();
    try {
      final active = await _ensureSession();
      final segment = await repository.appendFinalTranscript(
          active.id, clean, _nextSequence(active.id));
      final result = await repository.ask(active.id, segment.id);
      if (_disposed) return;
      turns = [
        ...turns,
        VoiceTurn(
          id: result.assistantSegmentId,
          speaker: VoiceSpeaker.assistant,
          text: result.reply,
          result: result,
          sessionId: active.id,
        ),
      ];
      pendingApprovals = result.workspace.pendingApprovals;
      if (muted || !_visible || result.reply.trim().isEmpty) {
        state = VoiceState.idle;
        _notify();
        _maybeListenForWake();
        return;
      }
      state = VoiceState.speaking;
      _notify();
      final token = ++_speakToken;
      try {
        // Some engines never report the end of speech; never get stuck.
        await speech.speak(result.reply, locale).timeout(Duration(
            milliseconds: math.min(30000, 4000 + result.reply.length * 80)));
      } on Object {
        // Speaking is best effort; the reply is on screen.
      }
      if (_disposed || token != _speakToken || state != VoiceState.speaking) {
        return;
      }
      state = VoiceState.idle;
      _notify();
      // Keep the conversation going: listen once for a follow-up without the
      // name. Proposals and visual-only replies wait for a tap instead.
      if (wakeEnabled && _visible && result.riskTier == VoiceRiskTier.read) {
        await startCapture(followUp: true);
      } else {
        _maybeListenForWake();
      }
    } on Object catch (error) {
      if (_disposed) return;
      state = VoiceState.idle;
      notice = _friendlyError(error);
      _notify();
      _maybeListenForWake();
    }
  }

  /// The user tapped "Create task" on a proposal. This is the only path that
  /// creates a task from voice.
  Future<void> confirmTask(VoiceTurn turn) async {
    final current = _turn(turn.id);
    if (current == null ||
        !current.awaitingConfirmation ||
        current.busy ||
        current.sessionId == null) {
      return;
    }
    final proposal = current.result!.proposedTask!;
    final sessionId = current.sessionId!;
    _replaceTurn(current.copyWith(busy: true));
    try {
      final segment = await repository.appendFinalTranscript(
          sessionId, proposal, _nextSequence(sessionId));
      final command = await repository.createTask(sessionId, segment.id,
          title: _title(proposal));
      _replaceTurn(current.copyWith(
          resolution: TurnResolution.created,
          busy: false,
          createdTitle: command.title));
      notice = isArabic
          ? 'تم إنشاء المهمة: ${command.title}'
          : 'Task created: ${command.title}';
    } on Object catch (error) {
      _replaceTurn(current.copyWith(busy: false));
      notice = _friendlyError(error);
    }
    _notify();
  }

  void dismiss(VoiceTurn turn) {
    final current = _turn(turn.id);
    if (current == null || current.resolution != null) return;
    _replaceTurn(current.copyWith(resolution: TurnResolution.dismissed));
    _notify();
  }

  /// Ends the conversation and asks the server to discard its transcript.
  Future<void> clearConversation() async {
    await _interruptSpeaking();
    if (state == VoiceState.listening) {
      _invalidateListening();
      await speech.cancel();
    }
    final active = session;
    session = null;
    _sessionLocale = null;
    _sessionName = null;
    turns = const [];
    heard = '';
    state = VoiceState.idle;
    notice = isArabic ? 'تم مسح المحادثة.' : 'Conversation cleared.';
    _notify();
    if (active != null) {
      try {
        await repository.cancel(active.id);
      } on Object {
        // The session expires on the server anyway.
      }
    }
    _maybeListenForWake();
  }

  // ------------------------------------------------------------ settings

  /// Returns false when the name has nothing usable left after cleaning.
  Future<bool> setAssistantName(String value) async {
    final clean = sanitizeAssistantName(value);
    if (clean.isEmpty) return false;
    if (clean == assistantName) return true;
    assistantName = clean;
    _notify();
    await preferences.write(_kName, clean);
    return true;
  }

  Future<void> toggleWake() async {
    wakeEnabled = !wakeEnabled;
    notice = null;
    if (!wakeEnabled) await _stopBackgroundListening();
    _notify();
    await preferences.write(_kWake, wakeEnabled ? 'on' : 'off');
    _maybeListenForWake();
  }

  Future<void> toggleMute() async {
    muted = !muted;
    if (muted && state == VoiceState.speaking) {
      await _interruptSpeaking();
      state = VoiceState.idle;
      _maybeListenForWake();
    }
    _notify();
    await preferences.write(_kMuted, muted ? 'on' : 'off');
  }

  Future<void> setLocale(String value) async {
    if (value == locale) return;
    locale = value;
    if (state == VoiceState.listening) {
      _invalidateListening();
      await speech.cancel();
      state = VoiceState.idle;
    } else {
      await _stopBackgroundListening();
    }
    _notify();
    await preferences.write(_kLocale, value);
    _maybeListenForWake();
  }

  Future<void> setRetention(VoiceRetention value) async {
    if (value == retention) return;
    retention = value;
    _notify();
    await preferences.write(_kRetention, value.apiValue);
  }

  Future<void> openSettings() async {
    _returningFromSettings = true;
    await speech.openSettings();
  }

  /// Retry after the user granted microphone access.
  void retryMicrophone() {
    micBlocked = false;
    speechUnavailable = false;
    notice = null;
    _notify();
    _maybeListenForWake();
  }

  // -------------------------------------------------- background listening

  void _maybeListenForWake() {
    if (_disposed ||
        !_visible ||
        !wakeEnabled ||
        micBlocked ||
        speechUnavailable ||
        state != VoiceState.idle ||
        wakeListening) {
      return;
    }
    unawaited(_startWake());
  }

  Future<void> _startWake() async {
    _restartTimer?.cancel();
    wakeListening = true;
    final token = ++_listenToken;
    if (!await _ensureSpeech()) {
      if (token == _listenToken) wakeListening = false;
      _notify();
      return;
    }
    if (token != _listenToken || _disposed) return;
    _wakeStartedAt = DateTime.now();
    _notify();
    _armWatchdog(_wakeFor + const Duration(seconds: 5), () {
      if (token != _listenToken) return;
      unawaited(speech.cancel());
      _wakeDone(token);
    });
    await speech.listen(
      locale: locale,
      pauseFor: const Duration(seconds: 3),
      listenFor: _wakeFor,
      phrases: wakeAliases(assistantName),
      onResult: (text, isFinal) {
        if (token != _listenToken || !isFinal) return;
        final rest = matchWakeWord(text, assistantName);
        if (rest != null) unawaited(_onWake(rest));
      },
      onError: (message, permanent) {
        if (token == _listenToken) _handleSpeechError(message);
      },
      onDone: () => _wakeDone(token),
    );
  }

  void _wakeDone(int token) {
    if (_disposed || token != _listenToken) return;
    _watchdog?.cancel();
    wakeListening = false;
    final started = _wakeStartedAt;
    final quick = started == null ||
        DateTime.now().difference(started) < const Duration(seconds: 1);
    _quickFailures = quick ? math.min(_quickFailures + 1, 10) : 0;
    // Restart with a growing pause if the recognizer keeps failing fast.
    _restartTimer?.cancel();
    _restartTimer = Timer(restartDelay * (1 + _quickFailures), () {
      if (_listenToken == token) _maybeListenForWake();
    });
    _notify();
  }

  Future<void> _onWake(String rest) async {
    _invalidateListening();
    await speech.cancel();
    if (_disposed) return;
    if (rest.isNotEmpty) {
      await ask(rest);
    } else {
      await startCapture();
    }
  }

  Future<void> _stopBackgroundListening() async {
    if (!wakeListening) {
      _restartTimer?.cancel();
      return;
    }
    _invalidateListening();
    await speech.cancel();
  }

  Future<void> _stopEverything() async {
    final wasListening = wakeListening || state == VoiceState.listening;
    _invalidateListening();
    _resetLevel();
    if (state == VoiceState.listening) {
      state = VoiceState.idle;
      heard = '';
      followUp = false;
    }
    if (state == VoiceState.speaking) {
      await _interruptSpeaking();
      state = VoiceState.idle;
    }
    _notify();
    if (wasListening) await speech.cancel();
  }

  // -------------------------------------------------------------- helpers

  Future<bool> _ensureSpeech() async {
    SpeechAvailability availability;
    try {
      availability = await speech.initialize();
    } on Object {
      availability = SpeechAvailability.unavailable;
    }
    switch (availability) {
      case SpeechAvailability.ready:
        if (micBlocked || speechUnavailable) notice = null;
        micBlocked = false;
        speechUnavailable = false;
        return true;
      case SpeechAvailability.denied:
        micBlocked = true;
        notice = isArabic
            ? 'الميكروفون غير مسموح. يمكنك الكتابة بدلاً من ذلك.'
            : 'Microphone access is off. You can still type to $assistantName.';
        return false;
      case SpeechAvailability.unavailable:
        speechUnavailable = true;
        notice = isArabic
            ? 'التعرف على الكلام غير متاح على هذا الجهاز. اكتب رسالتك بدلاً من ذلك.'
            : 'Speech recognition is not available on this device. Type your message instead.';
        return false;
    }
  }

  void _handleSpeechError(String message) {
    final value = message.toLowerCase();
    if (value.contains('permission')) {
      micBlocked = true;
      notice = isArabic
          ? 'الميكروفون غير مسموح. يمكنك الكتابة بدلاً من ذلك.'
          : 'Microphone access is off. You can still type to $assistantName.';
      _notify();
    }
    // no_match / speech_timeout are normal when nobody is talking.
  }

  Future<VoiceSession> _ensureSession() async {
    final current = session;
    if (current != null &&
        current.status == 'active' &&
        _sessionLocale == locale &&
        _sessionName == assistantName) {
      return current;
    }
    final created = await repository.createSession(
        locale: locale, retention: retention, assistantName: assistantName);
    session = created;
    _sessionLocale = locale;
    _sessionName = assistantName;
    return created;
  }

  int _nextSequence(String sessionId) {
    final next = _sequences[sessionId] ?? 0;
    _sequences[sessionId] = next + 1;
    return next;
  }

  Future<void> _interruptSpeaking() async {
    _speakToken++;
    if (state == VoiceState.speaking) {
      try {
        await speech.stopSpeaking();
      } on Object {
        // Nothing to stop.
      }
    }
  }

  void _invalidateListening() {
    _listenToken++;
    wakeListening = false;
    _restartTimer?.cancel();
    _watchdog?.cancel();
  }

  void _armWatchdog(Duration after, void Function() onFire) {
    _watchdog?.cancel();
    _watchdog = Timer(after, onFire);
  }

  void _setLevel(double raw) {
    _minLevel = math.min(_minLevel, raw);
    _maxLevel = math.max(_maxLevel, raw);
    final span = _maxLevel - _minLevel;
    level.value = span < 0.5 ? 0 : ((raw - _minLevel) / span).clamp(0.0, 1.0);
  }

  void _resetLevel() {
    _minLevel = double.infinity;
    _maxLevel = double.negativeInfinity;
    level.value = 0;
  }

  VoiceTurn? _turn(String id) {
    for (final turn in turns) {
      if (turn.id == id) return turn;
    }
    return null;
  }

  void _replaceTurn(VoiceTurn next) {
    turns = [for (final turn in turns) turn.id == next.id ? next : turn];
  }

  String _title(String value) {
    final clean = value.trim();
    return clean.length <= 160 ? clean : '${clean.substring(0, 157)}...';
  }

  String _friendlyError(Object error) {
    if (error is ApiException) {
      if (error.statusCode == 401) {
        return isArabic
            ? 'انتهت الجلسة. سجّل الدخول مرة أخرى.'
            : 'Your sign-in expired. Sign in again to keep talking.';
      }
      if (error.statusCode == 403) {
        return isArabic
            ? 'ليست لديك صلاحية لذلك.'
            : "You don't have permission for that.";
      }
      if (error.statusCode == 429) {
        return isArabic
            ? 'وصلت إلى حد الأسئلة لهذه المحادثة. امسحها وابدأ من جديد.'
            : 'This conversation reached its question limit. Clear it to start again.';
      }
      return error.message;
    }
    return isArabic
        ? 'تعذّر الرد. تحقق من الاتصال وحاول مرة أخرى.'
        : 'I could not answer. Check the connection and try again.';
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _invalidateListening();
    level.dispose();
    super.dispose();
  }
}
