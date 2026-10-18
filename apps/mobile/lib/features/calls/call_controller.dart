// The app-wide state of in-app calls (docs/in-app-calls.md#flutter): this
// phone's call stream (reconnecting with backoff), the incoming ring, the call
// in progress with its timer and heartbeats, and what ended it.
import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'call_media.dart';
import 'call_models.dart';
import 'call_repository.dart';

enum CallConfigPhase { loading, ready, denied, error }

enum CallPhase {
  idle,
  preparing,
  ringing,
  incoming,
  connecting,
  connected,
  reconnecting,
  ended
}

/// A message for the person; [key] is a [CallText] key.
class CallNotice {
  const CallNotice(this.key, [this.values = const {}]);
  final String key;
  final Map<String, Object> values;
}

class AppCallsController extends ChangeNotifier {
  AppCallsController({
    required this.repository,
    required this.mediaFactory,
    String? clientId,
    this.heartbeatEvery = const Duration(seconds: 15),
  }) : clientId = clientId ?? newCallClientId();

  final CallRepository repository;
  final CallMediaFactory mediaFactory;
  final String clientId;
  final Duration heartbeatEvery;

  CallConfigPhase configPhase = CallConfigPhase.loading;
  CallConfig? config;
  String? configError;
  bool online = true;

  /// The signed-in person, left out of the People list.
  String? selfUserId;

  CallPhase phase = CallPhase.idle;
  AppCall? call;
  CallPerson? peer;
  CallMediaKind media = CallMediaKind.audio;
  CallMediaSession? session;
  CallNotice? notice;
  bool muted = false;
  bool speaker = true;
  bool camera = true;

  /// Bumped when a call starts or ends, so the history reloads.
  int revision = 0;

  bool _offered = false;
  bool _running = false;
  int _failures = 0;
  StreamSubscription<CallStreamEvent>? _subscription;
  Timer? _reconnect;
  Timer? _heartbeat;
  Timer? _ticker;
  final Map<String, List<CallSignal>> _queued = {};

  /// Seconds since the call was answered (the timer).
  int get elapsed {
    final answered = call?.answeredAt;
    if (answered == null) return 0;
    final seconds = DateTime.now().difference(answered).inSeconds;
    return seconds < 0 ? 0 : seconds;
  }

  /// Why calls cannot be placed now, as a [CallText] key, or null.
  String? get unavailableKey {
    switch (configPhase) {
      case CallConfigPhase.loading:
        return null;
      case CallConfigPhase.denied:
        return 'denied';
      case CallConfigPhase.error:
        return 'configFailed';
      case CallConfigPhase.ready:
        if (!(config?.enabled ?? false)) return 'off';
        if (!online) return 'offline';
        return null;
    }
  }

  bool get busy => phase != CallPhase.idle && phase != CallPhase.ended;

  /// Signed in: read the server's call mode and, when calls are on, listen.
  Future<void> start() async {
    if (_running) return;
    _running = true;
    configPhase = CallConfigPhase.loading;
    notifyListeners();
    try {
      config = await repository.config();
      configPhase = CallConfigPhase.ready;
    } on ApiException catch (error) {
      configPhase = error.statusCode == 403
          ? CallConfigPhase.denied
          : CallConfigPhase.error;
      configError = error.message;
    } on Object catch (error) {
      configPhase = CallConfigPhase.error;
      configError = '$error';
    }
    notifyListeners();
    if (_running && (config?.enabled ?? false)) _listen();
  }

  void _listen() {
    _subscription?.cancel();
    _subscription = repository.stream(clientId).listen(_onEvent, onError: (_) {
      _failures += 1;
      _scheduleReconnect();
    }, onDone: () {
      _failures = 0;
      _scheduleReconnect();
    });
  }

  void _scheduleReconnect() {
    if (!_running) return;
    _reconnect?.cancel();
    final ms = _failures <= 0
        ? 500
        : (1000 * (1 << (_failures - 1))).clamp(1000, 15000);
    _reconnect = Timer(Duration(milliseconds: ms), () {
      if (_running) _listen();
    });
  }

  /// Network reachability from the platform (or tests).
  void setOnline(bool value) {
    if (online == value) return;
    online = value;
    notifyListeners();
  }

  void _onEvent(CallStreamEvent event) {
    _failures = 0;
    switch (event) {
      case CallChanged(:final call):
        _onCall(call);
      case SignalReceived(:final signal):
        final current = call;
        final media = session;
        if (current != null &&
            current.id == signal.callId &&
            media != null &&
            phase != CallPhase.incoming) {
          unawaited(media
              .handle(signal)
              .catchError((Object _) => hangUp(reason: 'failed')));
        } else {
          (_queued[signal.callId] ??= []).add(signal);
        }
    }
  }

  void _onCall(AppCall update) {
    final current = call;
    if (current != null && current.id == update.id) {
      if (phase == CallPhase.incoming) {
        if (update.status != CallStatus.ringing || update.answeredElsewhere) {
          _reset();
          if (update.answeredElsewhere) {
            notice = const CallNotice('answeredElsewhere');
          }
          revision += 1;
          notifyListeners();
        }
        return;
      }
      if (update.status == CallStatus.ended) {
        _finish(update);
        return;
      }
      call = update;
      if (!update.incoming &&
          update.status == CallStatus.accepted &&
          !_offered) {
        _offered = true;
        phase = CallPhase.connecting;
        unawaited(session
            ?.offer()
            .catchError((Object _) => hangUp(reason: 'failed')));
      }
      notifyListeners();
      return;
    }
    if (update.incoming &&
        update.status == CallStatus.ringing &&
        !update.answeredElsewhere) {
      if (busy) {
        unawaited(repository
            .end(update.id, 'busy')
            .then((_) {}, onError: (Object _) {}));
        return;
      }
      call = update;
      peer = CallPerson(userId: update.peerUserId, name: update.peerName);
      media = update.media;
      phase = CallPhase.incoming;
      notice = null;
      notifyListeners();
    } else if (update.status == CallStatus.ended) {
      revision += 1;
      notifyListeners();
    }
  }

  CallSignalSink _sink(String callId) => _ApiSink(repository, callId);

  void _linkChanged(CallLinkState state) {
    switch (state) {
      case CallLinkState.connecting:
        if (phase != CallPhase.ringing) phase = CallPhase.connecting;
      case CallLinkState.connected:
        phase = CallPhase.connected;
        _startHeartbeats();
      case CallLinkState.reconnecting:
        phase = CallPhase.reconnecting;
      case CallLinkState.failed:
        unawaited(hangUp(reason: 'failed'));
        return;
    }
    notifyListeners();
  }

  void _startHeartbeats() {
    final id = call?.id;
    if (id == null || _heartbeat != null) return;
    unawaited(repository.heartbeat(id).catchError((Object _) {}));
    _heartbeat = Timer.periodic(heartbeatEvery,
        (_) => unawaited(repository.heartbeat(id).catchError((Object _) {})));
    _ticker ??=
        Timer.periodic(const Duration(seconds: 1), (_) => notifyListeners());
  }

  /// Rings [person]: the microphone (and camera) first, so a refusal rings nobody.
  Future<void> place(CallPerson person, {bool video = false}) async {
    if (busy) {
      notice = const CallNotice('alreadyInCall');
      notifyListeners();
      return;
    }
    final unavailable = unavailableKey;
    if (unavailable != null) {
      notice = CallNotice(unavailable);
      notifyListeners();
      return;
    }
    peer = person;
    media = video ? CallMediaKind.video : CallMediaKind.audio;
    camera = video;
    phase = CallPhase.preparing;
    notice = null;
    call = null;
    notifyListeners();
    final media0 = mediaFactory();
    try {
      await media0.open(video: video);
    } on CallMediaException catch (error) {
      await media0.close();
      _reset();
      notice =
          CallNotice('media.${error.problem.name}', {'message': error.message});
      notifyListeners();
      return;
    }
    try {
      final placed = await repository.place(person.userId, media, clientId);
      if (placed.status == CallStatus.ended) {
        await media0.close();
        session = null;
        _finish(placed);
        return;
      }
      session = media0;
      call = placed;
      phase = CallPhase.ringing;
      _offered = false;
      revision += 1;
      notifyListeners();
      await media0.connect(
          caller: true,
          iceServers: await repository.iceServers(),
          sink: _sink(placed.id),
          onState: _linkChanged);
    } on Object catch (error) {
      await media0.close();
      final placedId = call?.id;
      _reset();
      if (placedId != null) {
        unawaited(repository
            .end(placedId, 'failed')
            .then((_) {}, onError: (Object _) {}));
      }
      notice = CallNotice('startFailed',
          {'message': error is ApiException ? error.message : '$error'});
      notifyListeners();
    }
  }

  Future<void> answer({bool video = false}) async {
    final ringing = call;
    if (phase != CallPhase.incoming || ringing == null) return;
    phase = CallPhase.connecting;
    camera = video && ringing.video;
    notifyListeners();
    final media0 = mediaFactory();
    try {
      await media0.open(video: video && ringing.video);
    } on CallMediaException catch (error) {
      await media0.close();
      unawaited(repository
          .end(ringing.id, 'failed')
          .then((_) {}, onError: (Object _) {}));
      _reset();
      notice =
          CallNotice('media.${error.problem.name}', {'message': error.message});
      revision += 1;
      notifyListeners();
      return;
    }
    try {
      session = media0;
      await media0.connect(
          caller: false,
          iceServers: await repository.iceServers(),
          sink: _sink(ringing.id),
          onState: _linkChanged);
      call = await repository.accept(ringing.id, clientId);
      for (final signal in _queued.remove(ringing.id) ?? const <CallSignal>[]) {
        await media0.handle(signal);
      }
      revision += 1;
      notifyListeners();
    } on Object catch (error) {
      await media0.close();
      _reset();
      notice = CallNotice('answerFailed',
          {'message': error is ApiException ? error.message : '$error'});
      revision += 1;
      notifyListeners();
    }
  }

  Future<void> decline() async {
    final ringing = call;
    if (phase != CallPhase.incoming || ringing == null) return;
    _reset();
    notifyListeners();
    try {
      await repository.end(ringing.id, 'decline');
    } on Object {
      // The ring times out on the server anyway.
    }
    revision += 1;
    notifyListeners();
  }

  Future<void> hangUp({String reason = 'hangup'}) async {
    final current = call;
    if (current == null) {
      if (phase == CallPhase.preparing) return;
      _reset();
      notifyListeners();
      return;
    }
    try {
      _finish(await repository.end(current.id, reason));
    } on Object {
      _finish(AppCall(
        id: current.id,
        incoming: current.incoming,
        peerUserId: current.peerUserId,
        peerName: current.peerName,
        media: current.media,
        status: CallStatus.ended,
        outcome: reason == 'failed' ? 'failed' : 'completed',
        createdAt: current.createdAt,
        answeredAt: current.answeredAt,
      ));
    }
  }

  void toggleMute() {
    muted = !muted;
    session?.setMuted(muted);
    notifyListeners();
  }

  void toggleSpeaker() {
    speaker = !speaker;
    unawaited(session?.setSpeaker(speaker).catchError((Object _) {}));
    notifyListeners();
  }

  void toggleCamera() {
    camera = !camera;
    session?.setCamera(camera);
    notifyListeners();
  }

  void dismiss() {
    if (phase == CallPhase.ended) _reset();
    notice = null;
    notifyListeners();
  }

  void clearNotice() {
    notice = null;
    notifyListeners();
  }

  void _finish(AppCall ended) {
    _stopTimers();
    final media0 = session;
    session = null;
    unawaited(media0?.close());
    call = ended;
    peer ??= CallPerson(userId: ended.peerUserId, name: ended.peerName);
    phase = CallPhase.ended;
    revision += 1;
    notifyListeners();
  }

  void _stopTimers() {
    _heartbeat?.cancel();
    _heartbeat = null;
    _ticker?.cancel();
    _ticker = null;
  }

  void _reset() {
    _stopTimers();
    final media0 = session;
    session = null;
    unawaited(media0?.close());
    call = null;
    peer = null;
    phase = CallPhase.idle;
    muted = false;
    speaker = true;
    _offered = false;
  }

  /// Signed out: stop listening and forget everything.
  void stop() {
    _running = false;
    _reconnect?.cancel();
    unawaited(_subscription?.cancel());
    _subscription = null;
    _reset();
    _queued.clear();
    config = null;
    configPhase = CallConfigPhase.loading;
    notice = null;
    notifyListeners();
  }

  @override
  void dispose() {
    stop();
    super.dispose();
  }
}

class _ApiSink implements CallSignalSink {
  _ApiSink(this.repository, this.callId);
  final CallRepository repository;
  final String callId;

  @override
  Future<void> description(String kind, String sdp) =>
      repository.sendDescription(callId, kind, sdp);

  @override
  Future<void> candidates(List<Map<String, Object?>> batch) =>
      repository.sendCandidates(callId, batch);
}
